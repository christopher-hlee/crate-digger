"""Moving a crate to another machine.

Stored paths are absolute, so a crate copied to a new laptop — or merely to a
different username — refers to files that are not there. Nothing rewrote them,
which made "copy your library across" quietly wrong advice.
"""
from __future__ import annotations

import pytest

from crate.cli import main
from crate.config import reset_settings_cache
from crate.db import Database


@pytest.fixture(autouse=True)
def _fresh_settings():
    """Settings are cached per process and these tests each use a new library."""
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def moved_crate(tmp_path, monkeypatch):
    """A library on disk whose database still talks about the old machine.

    Written to be safe to run twice over the same directory: pytest-asyncio's
    auto mode re-runs sync fixtures during the call phase, and a fixture that
    assumed it ran once failed on its own second run rather than on anything
    the product did.
    """
    library = tmp_path / "CrateDigger"
    for sub in ("audio", "slices", "loops", "picked"):
        (library / sub).mkdir(parents=True, exist_ok=True)
    (library / "audio" / "record.mp3").write_bytes(b"audio")
    (library / "picked" / "break.wav").write_bytes(b"wav")

    monkeypatch.setenv("CRATE_LIBRARY_DIR", str(library))
    reset_settings_cache()

    stale = "/Users/someone-else/CrateDigger"
    db = Database(library / "crate.sqlite3")
    sample_id = db.upsert_sample(
        {"source": "ia", "source_id": "x", "title": "Old Record",
         "status": "ready", "file_path": f"{stale}/audio/record.mp3"}
    )
    db.execute("UPDATE samples SET file_path = ? WHERE id = ?",
               [f"{stale}/audio/record.mp3", sample_id])
    db.execute(
        "INSERT OR REPLACE INTO picks "
        "(id, sample_id, idx, start_sec, end_sec, file_path, created_at) "
        "VALUES (1, ?, 0, 0.0, 4.0, ?, 0)",
        [sample_id, f"{stale}/picked/break.wav"],
    )
    db.close()
    return library


def stored(library, table="samples"):
    db = Database(library / "crate.sqlite3")
    try:
        return db.one(f"SELECT file_path FROM {table} WHERE id = 1")["file_path"]
    finally:
        db.close()


def test_a_crate_from_another_machine_is_repointed(moved_crate, capsys):
    assert main(["relocate"]) == 0
    assert stored(moved_crate) == str(moved_crate / "audio" / "record.mp3")
    # picks travel with the crate and are just as broken, so they move too.
    assert stored(moved_crate, "picks") == str(moved_crate / "picked" / "break.wav")
    assert "2 repointed" in capsys.readouterr().out


def test_dry_run_writes_nothing(moved_crate, capsys):
    assert main(["relocate", "--dry-run"]) == 0
    assert stored(moved_crate).startswith("/Users/someone-else")
    out = capsys.readouterr().out
    assert "would be repointed" in out


def test_paths_already_correct_are_left_alone(moved_crate, capsys):
    main(["relocate"])
    capsys.readouterr()
    assert main(["relocate"]) == 0
    out = capsys.readouterr().out
    assert "2 already correct" in out and "0 repointed" in out


def test_a_record_whose_audio_was_not_copied_is_named_not_repointed(
    moved_crate, capsys
):
    (moved_crate / "audio" / "record.mp3").unlink()
    assert main(["relocate"]) == 0
    out = capsys.readouterr().out
    assert "no file at" in out and "crate refetch" in out
    # Left pointing at the old path rather than at a file that is not there.
    assert stored(moved_crate).startswith("/Users/someone-else")


def test_a_path_outside_the_known_subfolders_is_reported_not_guessed(
    moved_crate, capsys
):
    db = Database(moved_crate / "crate.sqlite3")
    db.execute("UPDATE samples SET file_path = ? WHERE id = 1",
               ["/somewhere/entirely/else.mp3"])
    db.close()
    assert main(["relocate"]) == 0
    out = capsys.readouterr().out
    assert "cannot place" in out and "1 unplaceable" in out
