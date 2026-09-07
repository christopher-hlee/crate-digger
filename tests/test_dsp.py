import numpy as np
import pytest

from crate.audio import decode, dsp

from .conftest import make_audio


@pytest.mark.parametrize("bpm", [72, 85, 90, 100, 120, 140])
def test_tempo_is_within_two_bpm(bpm):
    y, sr = make_audio(bpm=bpm, seconds=20)
    assert dsp.estimate_tempo(y, sr).bpm == pytest.approx(bpm, abs=2.0)


def test_tempo_does_not_latch_onto_the_detrend_window():
    """Regression: a 1s detrend window put a false peak at exactly 60 BPM."""
    y, sr = make_audio(bpm=120, seconds=20)
    assert dsp.estimate_tempo(y, sr).bpm > 100


def test_tempo_of_silence_is_zero():
    est = dsp.estimate_tempo(np.zeros(22050, dtype=np.float32), 22050)
    assert est.bpm == 0.0 and est.confidence == 0.0


@pytest.mark.parametrize(
    "freqs,expected",
    [
        ((220.0, 261.63, 329.63), "A minor"),
        ((261.63, 329.63, 392.0), "C major"),
    ],
)
def test_key_of_a_triad(freqs, expected):
    sr = 22050
    t = np.arange(sr * 6) / sr
    y = sum(np.sin(2 * np.pi * f * t) for f in freqs).astype(np.float32) / len(freqs)
    est = dsp.estimate_key(y, sr)
    assert est.key == expected
    assert est.camelot
    assert 0.0 <= est.confidence <= 1.0


def test_onsets_land_on_the_grid():
    y, sr = make_audio(bpm=120, seconds=8)   # a hit every 0.5s
    times = dsp.refine_onsets(y, sr, dsp.detect_onsets(y, sr))
    assert len(times) >= 12
    for t in times:
        assert min(abs(t - i * 0.5) for i in range(16)) < 0.02


def test_refined_onsets_sit_on_zero_crossings():
    y, sr = make_audio(bpm=100, seconds=8)
    times = dsp.refine_onsets(y, sr, dsp.detect_onsets(y, sr))
    assert times.size
    for t in times[:6]:
        assert abs(y[int(t * sr)]) < 0.05


def test_breakiness_separates_drums_from_drones():
    sr = 22050
    t = np.arange(sr * 8) / sr
    drone = (0.4 * np.sin(2 * np.pi * 180 * t)).astype(np.float32)
    drums, _ = make_audio(bpm=95, seconds=8, tone_hz=0.0)
    assert dsp.breakiness(drums) > dsp.breakiness(drone)
    assert 0.0 <= dsp.breakiness(drone) <= 1.0


def test_scalar_outputs_are_plain_floats():
    """Numpy scalars break JSON encoding on the way out of the API."""
    y, sr = make_audio()
    assert type(dsp.loudness_db(y)) is float
    assert type(dsp.breakiness(y)) is float
    assert all(type(p) is float for p in dsp.peaks(y, 32))


def test_peaks_are_normalised_and_sized():
    y, _ = make_audio()
    peaks = dsp.peaks(y, 200)
    assert len(peaks) == 200
    assert max(peaks) == pytest.approx(1.0)
    assert min(peaks) >= 0.0


def test_peaks_of_empty_input():
    assert dsp.peaks(np.zeros(0, dtype=np.float32)) == []


def test_varispeed_changes_length_and_pitch_together():
    y, sr = make_audio(seconds=4)
    faster = decode.change_speed(y, 2.0)
    assert len(faster) == pytest.approx(len(y) / 2, rel=0.01)


# ── finding the break ─────────────────────────────────────────────────

def make_record_with_a_break(sr=22050, seconds=30, break_from=11.0, break_to=19.0):
    """A band playing, which drops out to leave the drummer alone."""
    t = np.arange(int(sr * seconds)) / sr
    y = (0.35 * np.sin(2 * np.pi * 220 * t) + 0.3 * np.sin(2 * np.pi * 277 * t))
    y[int(break_from * sr):int(break_to * sr)] *= 0.06
    for i in range(int(seconds / 0.5)):
        start = int(i * 0.5 * sr)
        n = int(0.05 * sr)
        hit = np.random.RandomState(i).randn(n) * np.exp(-np.linspace(0, 7, n))
        y[start:start + n] += hit * 0.55
    return y.astype(np.float32), sr


def test_find_breaks_locates_the_drum_only_stretch():
    y, sr = make_record_with_a_break()
    found = dsp.find_breaks(y, sr)
    assert found, "a record with an obvious break should yield one"
    top = found[0]
    assert top["start_sec"] < 13.0 and top["end_sec"] > 17.0
    assert top["lift"] > 0            # above this record's own baseline
    assert 0.0 <= top["score"] <= 1.0


def test_find_breaks_returns_json_safe_scalars():
    y, sr = make_record_with_a_break()
    import json
    assert json.dumps(dsp.find_breaks(y, sr))


def test_a_record_that_never_drops_out_has_no_standout_break():
    """A steady full-band take should not invent a break."""
    sr = 22050
    t = np.arange(sr * 20) / sr
    y = (0.35 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    for i in range(40):
        s = int(i * 0.5 * sr)
        n = int(0.05 * sr)
        y[s:s + n] += np.random.RandomState(i).randn(n) * np.exp(-np.linspace(0, 7, n)) * 0.5
    found = dsp.find_breaks(y, sr, min_length=3.0)
    assert all(r["length_sec"] < 15 for r in found)


def test_find_breaks_respects_min_length_and_cap():
    y, sr = make_record_with_a_break()
    assert dsp.find_breaks(y, sr, min_length=60.0) == []
    assert len(dsp.find_breaks(y, sr, max_results=1)) <= 1


def test_find_breaks_on_something_too_short():
    assert dsp.find_breaks(np.zeros(500, dtype=np.float32), 22050) == []


def test_percussive_curve_tracks_the_drop_out():
    y, sr = make_record_with_a_break()
    times, ratio = dsp.percussive_curve(y, sr)
    assert times.size == ratio.size and times.size > 10
    during = ratio[(times > 12) & (times < 18)].mean()
    outside = ratio[(times < 9) | (times > 21)].mean()
    assert during > outside
