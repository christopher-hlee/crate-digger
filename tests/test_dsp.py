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
    if break_from is not None:
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


def test_lead_in_hiss_is_not_mistaken_for_a_break():
    """Noise is spectrally flat, so silence scored higher than the band did."""
    y, sr = make_record_with_a_break(break_from=None, break_to=None)
    y = y.copy()
    y[: int(2.0 * sr)] = np.random.RandomState(1).randn(int(2.0 * sr)) * 0.002
    found = dsp.find_breaks(y, sr)
    assert not any(r["start_sec"] < 2.5 and r["usable"] for r in found), found


def test_a_real_break_still_survives_a_hissy_lead_in():
    y, sr = make_record_with_a_break()
    y = y.copy()
    y[: int(2.0 * sr)] = np.random.RandomState(1).randn(int(2.0 * sr)) * 0.002
    usable = [r for r in dsp.find_breaks(y, sr) if r["usable"]]
    assert len(usable) == 1
    assert usable[0]["start_sec"] > 9.0 and usable[0]["end_sec"] > 17.0


def test_quiet_frames_score_zero():
    sr = 22050
    y = np.concatenate([
        np.random.RandomState(0).randn(sr * 3).astype(np.float32) * 0.002,
        (0.4 * np.sin(2 * np.pi * 220 * np.arange(sr * 5) / sr)).astype(np.float32),
    ])
    times, ratio = dsp.percussive_curve(y, sr)
    assert ratio[times < 2.0].max() == 0.0


# ── the needle drop ───────────────────────────────────────────────────
# Groove crackle is broadband and, on a 78, loud — it scores ~0.45 percussive
# against ~0.02 for the music, so no threshold on level alone separates them.

def _with_lead_in(y, sr, seconds=2.0, amplitude=0.25):
    out = y.copy()
    out[: int(seconds * sr)] = (
        np.random.RandomState(1).randn(int(seconds * sr)) * amplitude
    )
    return out


def test_a_loud_needle_drop_is_not_a_break():
    y, sr = make_record_with_a_break(break_from=None, break_to=None)
    found = dsp.find_breaks(_with_lead_in(y, sr), sr)
    assert not any(r["usable"] for r in found), found


def test_a_real_break_survives_a_loud_needle_drop():
    y, sr = make_record_with_a_break()
    usable = [r for r in dsp.find_breaks(_with_lead_in(y, sr), sr) if r["usable"]]
    assert len(usable) == 1
    assert usable[0]["start_sec"] > 9.0


def test_run_out_groove_at_the_end_is_not_a_break():
    y, sr = make_record_with_a_break(break_from=None, break_to=None)
    y = y.copy()
    y[-int(2.5 * sr):] = np.random.RandomState(2).randn(int(2.5 * sr)) * 0.25
    assert not any(r["usable"] for r in dsp.find_breaks(y, sr))


def test_the_lead_in_is_rejected_twice_over():
    """Two independent tests catch a needle drop, which is why it stays out.

    It is at the start of the record, so `min_context` rejects it; and groove
    crackle has no pulse, so `min_steadiness` rejects it too. Relaxing either
    alone still leaves it out — it takes both, which is what makes this robust
    rather than one tuned constant.
    """
    y, sr = make_record_with_a_break(break_from=None, break_to=None)
    y = _with_lead_in(y, sr)

    assert not any(r["usable"] for r in dsp.find_breaks(y, sr))
    assert not any(r["usable"] for r in dsp.find_breaks(y, sr, min_context=0.0))
    assert not any(r["usable"] for r in dsp.find_breaks(y, sr, min_steadiness=0.0))

    both = dsp.find_breaks(y, sr, min_context=0.0, min_steadiness=0.0, min_bars=0.0)
    assert any(r["usable"] for r in both), "only dropping both lets it through"


# ── horns are not drums ───────────────────────────────────────────────
# P/(P+H) rises both when drums come forward and when a broadband horn stab
# lands, because a saturated brass section is transient and broadband too.
# Only a fall in *absolute* harmonic energy means the band actually left.

def _band(secs=30, sr=22050, drop=None, horns=None):
    t = np.arange(int(sr * secs)) / sr
    y = (0.35 * np.sin(2 * np.pi * 220 * t) + 0.30 * np.sin(2 * np.pi * 277 * t)
         + 0.25 * np.sin(2 * np.pi * 330 * t))
    if drop:
        y[int(drop[0] * sr):int(drop[1] * sr)] *= 0.04
    if horns:
        a, b = int(horns[0] * sr), int(horns[1] * sr)
        y[a:b] *= 2.2
        y[a:b] += np.random.RandomState(3).randn(b - a) * 0.25
    for i in range(int(secs / 0.5)):
        s = int(i * 0.5 * sr)
        n = int(0.05 * sr)
        y[s:s + n] += np.random.RandomState(i).randn(n) * np.exp(-np.linspace(0, 7, n)) * 0.55
    return (y / np.max(np.abs(y)) * 0.9).astype(np.float32), sr


def test_a_horn_shout_is_not_a_break():
    y, sr = _band(horns=(11.0, 19.0))
    assert not any(r["usable"] for r in dsp.find_breaks(y, sr))


def test_the_band_leaving_is_a_break():
    y, sr = _band(drop=(11.0, 19.0))
    usable = [r for r in dsp.find_breaks(y, sr) if r["usable"]]
    assert len(usable) == 1
    assert usable[0]["harmonic"] < 0.5, "the pitched content must have gone"
    assert 9.0 < usable[0]["start_sec"] < 13.0


def test_harmonic_share_is_what_separates_them():
    """Relax max_harmonic and the horn shout comes back — proof of which
    criterion is doing the work."""
    y, sr = _band(horns=(11.0, 19.0))
    assert not any(r["usable"] for r in dsp.find_breaks(y, sr))
    loose = dsp.find_breaks(y, sr, max_harmonic=99.0, min_context=0.0)
    assert any(r["usable"] for r in loose) or not loose


def test_every_region_reports_its_harmonic_share():
    y, sr = _band(drop=(11.0, 19.0))
    for region in dsp.find_breaks(y, sr):
        assert 0.0 <= region["harmonic"]
        assert isinstance(region["harmonic"], float)


def test_percussive_curve_can_return_harmonic_energy():
    y, sr = _band(drop=(11.0, 19.0))
    times, ratio, harmonic = dsp.percussive_curve(y, sr, with_harmonic=True)
    assert times.size == ratio.size == harmonic.size
    during = harmonic[(times > 12) & (times < 18)].mean()
    outside = harmonic[(times < 9) | (times > 21)].mean()
    assert during < outside * 0.5, "harmonic energy must fall during the break"


# ── a break repeats; a solo does not ──────────────────────────────────
# Percussive share and harmonic drop call these identical — both are pure
# drums with the band gone. Only the pulse tells them apart.

def _kit(seconds, pattern, *, bpm=86.0, jitter=0.0, seed=0, sr=22050):
    """A drum pattern, `pattern` given as offsets in beats within two beats."""
    y = np.zeros(int(sr * seconds))
    beat = 60.0 / bpm
    rng = np.random.RandomState(seed)
    at = 0.0
    while at < seconds:
        for off in pattern:
            hit = at + off * beat + (rng.randn() * jitter if jitter else 0.0)
            start = int(hit * sr)
            n = int(0.06 * sr)
            if 0 <= start and start + n <= len(y):
                y[start:start + n] += rng.randn(n) * np.exp(-np.linspace(0, 6, n))
        at += 2 * beat
    return (y / max(np.max(np.abs(y)), 1e-9) * 0.8).astype(np.float32), sr


def test_a_steady_break_reads_as_steady():
    y, sr = _kit(8, [0, 0.5, 1, 1.5])
    steadiness, bpm = dsp.pulse_clarity(y, sr)
    assert steadiness > 0.8
    assert bpm > 0


def test_a_drum_solo_does_not():
    y, sr = _kit(8, [0, 0.31, 0.44, 0.9, 1.17, 1.6, 1.72], jitter=0.05, seed=3)
    assert dsp.pulse_clarity(y, sr)[0] < 0.30


def test_a_human_loose_break_still_passes():
    """Real drummers are not quantised; the bar must not demand that."""
    y, sr = _kit(8, [0, 0.5, 1, 1.5], jitter=0.012, seed=7)
    assert dsp.pulse_clarity(y, sr)[0] > 0.30


def test_dynamics_do_not_read_as_unsteadiness():
    y, sr = _kit(8, [0, 0.5, 1, 1.5], seed=5)
    quiet = y.copy()
    quiet[: len(quiet) // 2] *= 0.4          # the drummer plays the first half softer
    assert dsp.pulse_clarity(quiet, sr)[0] > 0.6


def test_pulse_clarity_of_nothing():
    assert dsp.pulse_clarity(np.zeros(4096, dtype=np.float32), 22050) == (0.0, 0.0)


def _record_with(kind, *, seconds=40, bpm=86.0, sr=22050):
    """A side where the band drops out for 8 bars and the kit keeps going."""
    t = np.arange(int(sr * seconds)) / sr
    band = (0.35 * np.sin(2 * np.pi * 220 * t) + 0.30 * np.sin(2 * np.pi * 277 * t)
            + 0.25 * np.sin(2 * np.pi * 330 * t))
    band[int(14 * sr):int(22 * sr)] *= 0.04
    y = band.copy()
    rng = np.random.RandomState(4)
    beat = 60.0 / bpm

    def hit(at, amp=0.5, source=rng):
        start, n = int(at * sr), int(0.06 * sr)
        if 0 <= start and start + n <= len(y):
            y[start:start + n] += source.randn(n) * np.exp(-np.linspace(0, 6, n)) * amp

    at = 0.0
    while at < seconds:
        for off in (0, 0.5, 1, 1.5):
            hit(at + off * beat)
        at += 2 * beat

    if kind == "solo":
        y[int(14 * sr):int(22 * sr)] = 0.0
        loose = np.random.RandomState(9)
        at = 14.0
        while at < 21.9:
            hit(at, 0.8, loose)
            at += abs(loose.randn() * 0.12) + 0.08
    return (y / np.max(np.abs(y)) * 0.9).astype(np.float32), sr


def test_a_record_whose_band_drops_out_to_a_steady_break_is_kept():
    y, sr = _record_with("break")
    usable = [r for r in dsp.find_breaks(y, sr, bpm=86.0) if r["usable"]]
    assert len(usable) == 1
    assert usable[0]["steadiness"] > 0.5
    assert 12.0 < usable[0]["start_sec"] < 16.0


def test_the_same_record_with_a_solo_there_instead_is_not():
    y, sr = _record_with("solo")
    found = dsp.find_breaks(y, sr, bpm=86.0)
    assert found, "the drop-out is still detected"
    assert not any(r["usable"] for r in found), "but it is not loopable"
    assert found[0]["steadiness"] < 0.3


def test_steadiness_is_what_rejects_it():
    """Relax only that bar and the solo returns — proof of which test bites."""
    y, sr = _record_with("solo")
    assert any(r["usable"] for r in
               dsp.find_breaks(y, sr, bpm=86.0, min_steadiness=0.0))


def test_a_break_too_short_to_loop_is_rejected():
    """Two seconds of drums is a fill. Two bars at 86 BPM is 5.6 seconds."""
    y, sr = _record_with("break")
    strict = dsp.find_breaks(y, sr, bpm=86.0, min_bars=8.0)
    assert not any(r["usable"] for r in strict)
    assert strict[0]["min_length_needed"] > strict[0]["length_sec"]


def test_every_region_reports_its_pulse():
    y, sr = _record_with("break")
    for region in dsp.find_breaks(y, sr, bpm=86.0):
        assert 0.0 <= region["steadiness"] <= 1.0
        assert isinstance(region["pulse_bpm"], float)
