"""Break hunting.

Digging by hand means auditioning a hundred records to find the four bars where
the band drops out. This does that pass for you: pull records from a seam,
analyse each one, keep only the ones with a real break in them, and throw the
rest back.

The keeper test is `dsp.find_breaks`, so it is about *lift* — how far the drums
rise above that record's own baseline — not about how loud the drums are. A
marching band is percussive from end to end and never qualifies; a soul side
where the horns drop out for four bars does.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

from . import library
from .audio import chop as chopper
from .audio import dsp
from .audio.cache import CACHE
from .config import Settings
from .db import Database
from .sources.base import Lead, SourceError

#: A radio show or a lecture is never a sample source, and downloading one
#: burns the size cap for nothing.
DEFAULT_MAX_DURATION = 12 * 60


@dataclass
class HuntReport:
    dig: str
    examined: int = 0
    kept: int = 0
    skipped_long: int = 0
    no_break: int = 0
    errors: int = 0
    records: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dig": self.dig,
            "examined": self.examined,
            "kept": self.kept,
            "skipped_long": self.skipped_long,
            "no_break": self.no_break,
            "errors": self.errors,
            "records": self.records,
        }


def _too_long(lead: Lead, limit: float | None) -> bool:
    return bool(limit and lead.duration and lead.duration > limit)


def _discard(db: Database, settings: Settings, row: dict) -> None:
    """Throw a record back: forget the row, remove the file it pulled down."""
    path = row.get("file_path")
    if path:
        candidate = Path(path)
        try:
            inside = candidate.resolve().is_relative_to(settings.library_dir.resolve())
        except OSError:
            inside = False
        if inside and candidate.is_file():
            candidate.unlink(missing_ok=True)
    db.delete_sample(row["id"])


def export_breaks(
    settings: Settings, row: dict, regions: list[dict], *, to_export_dir: bool = False
) -> list[dict]:
    """Render each usable break to its own WAV."""
    path = Path(row["file_path"])
    y, sr = CACHE.load(path)
    stem = library.slugify(
        " ".join(x for x in (row.get("artist"), row.get("title")) if x) or "break"
    )
    written = []
    for i, region in enumerate(r for r in regions if r.get("usable")):
        seg = chopper.take(y, sr, region["start_sec"], region["end_sec"])
        name = f"{stem}-break-{i + 1:02d}.wav"
        target = chopper.write_wav(settings.loops_dir / name, seg, sr)
        if to_export_dir and settings.export_dir:
            import shutil

            shutil.copy2(target, Path(settings.export_dir) / name)
        written.append({**region, "filename": name, "path": str(target)})
    return written


async def hunt(
    db: Database,
    client: httpx.AsyncClient,
    registry,
    settings: Settings,
    *,
    dig,
    want: int = 8,
    max_examine: int = 40,
    max_duration: float | None = DEFAULT_MAX_DURATION,
    min_lift: float = 0.08,
    rows: int = 30,
    page: int | None = None,
    export: bool = True,
    to_export_dir: bool = False,
    on_progress: Callable[[str], None] | None = None,
) -> HuntReport:
    """Work a seam until `want` records with real breaks have been found."""
    from . import digs as digs_module

    report = HuntReport(dig=dig.slug)
    source = registry.get(dig.source)

    params = dict(dig.params)
    query = params.pop("q", params.pop("query", ""))
    say = on_progress or (lambda _m: None)

    seen = db.seen_ids(dig.source)
    chosen_page = page or digs_module.random_page(dig)

    try:
        leads = await source.search(query, **params, rows=rows, page=chosen_page)
        total = getattr(source, "last_total", 0)
        if not leads and total:
            last_page = max(1, -(-total // rows))
            chosen_page = min(chosen_page, last_page)
            leads = await source.search(query, **params, rows=rows, page=chosen_page)
    except SourceError as exc:
        raise

    for lead in leads:
        if report.kept >= want or report.examined >= max_examine:
            break
        if lead.source_id in seen:
            continue

        # An Archive hit is an item; the audio is one level down.
        try:
            if lead.source == "ia" and not lead.stream_url:
                tracks = await registry.ia.tracks(lead.source_id)
            else:
                tracks = [lead]
        except SourceError:
            report.errors += 1
            continue
        if not tracks:
            continue

        track = next((t for t in tracks if not _too_long(t, max_duration)), None)
        if track is None:
            report.skipped_long += 1
            db.record_verdict(lead.source, lead.source_id, "pass")
            say(f"skipped {lead.title[:40]} — too long to be a record")
            continue

        report.examined += 1
        say(f"listening to {track.title[:44]}")
        row = await library.ingest_lead(
            db, client, track,
            audio_dir=settings.audio_dir, max_mb=settings.max_download_mb,
        )
        if row.get("status") != "ready" or not row.get("file_path"):
            report.errors += 1
            if row.get("id"):
                _discard(db, settings, row)
            continue

        # Analysis already looked for breaks; re-gate with this hunt's bar.
        y, sr = CACHE.load(Path(row["file_path"]), sr=22050)
        regions = dsp.find_breaks(y, sr, min_lift=min_lift)
        db.update_sample(row["id"], breaks=regions)

        if not dsp.has_usable_break(regions):
            report.no_break += 1
            db.record_verdict(lead.source, lead.source_id, "pass")
            _discard(db, settings, row)
            say(f"no break in {track.title[:40]} — thrown back")
            continue

        db.record_verdict(lead.source, lead.source_id, "keep")
        report.kept += 1
        best = max(regions, key=lambda r: r["lift"])
        entry = {
            "sample_id": row["id"],
            "title": row.get("title"),
            "artist": row.get("artist"),
            "year": row.get("year"),
            "bpm": row.get("bpm"),
            "break": best,
            "breaks": [r for r in regions if r.get("usable")],
        }
        if export:
            entry["files"] = export_breaks(
                settings, row, regions, to_export_dir=to_export_dir
            )
        report.records.append(entry)
        say(f"kept {track.title[:40]} — break at {best['start_sec']:.0f}s")

    return report
