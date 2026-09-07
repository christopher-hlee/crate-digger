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

import shutil
import signal
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

#: Leave this much of the disk alone. On a box shared with something that
#: matters, filling the disk is the failure that takes the neighbour down with
#: you: SQLite cannot write, and a monitor that cannot write is a monitor that
#: has silently stopped.
DEFAULT_MIN_FREE_GB = 5.0


def free_gb(path) -> float:
    return shutil.disk_usage(str(path)).free / 1e9


class StopRequested(Exception):
    """SIGTERM arrived — finish the record in hand and put the tools down."""


class Stopper:
    """Turns SIGTERM/SIGINT into a flag checked between records.

    Killing a hunt mid-download leaves a part-file and a half-written row.
    Between records everything is already committed, so that is where to stop.
    """

    def __init__(self) -> None:
        self.stop = False
        self._previous: dict = {}

    def __enter__(self) -> "Stopper":
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                self._previous[sig] = signal.getsignal(sig)
                signal.signal(sig, self._handle)
            except ValueError:      # not on the main thread; the API path
                pass
        return self

    def _handle(self, *_args) -> None:
        self.stop = True

    def __exit__(self, *_exc) -> None:
        for sig, handler in self._previous.items():
            try:
                signal.signal(sig, handler)
            except ValueError:
                pass


@dataclass
class HuntReport:
    dig: str
    examined: int = 0
    kept: int = 0
    skipped_long: int = 0
    no_break: int = 0
    errors: int = 0
    stopped: str = ""
    records: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dig": self.dig,
            "examined": self.examined,
            "kept": self.kept,
            "skipped_long": self.skipped_long,
            "no_break": self.no_break,
            "errors": self.errors,
            "stopped": self.stopped,
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
    settings: Settings,
    row: dict,
    regions: list[dict],
    *,
    to_export_dir: bool = False,
    max_export: int = 2,
) -> list[dict]:
    """Render the best usable breaks to WAV.

    Capped deliberately: five three-second files per record is not a crate,
    it is clutter. Regions arrive sorted by lift, so the first is the one the
    record is worth keeping for.
    """
    path = Path(row["file_path"])
    y, sr = CACHE.load(path)
    stem = library.slugify(
        " ".join(x for x in (row.get("artist"), row.get("title")) if x) or "break"
    )
    written = []
    usable = [r for r in regions if r.get("usable")][:max_export]
    for i, region in enumerate(usable):
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
    max_pages: int = 12,
    max_duration: float | None = DEFAULT_MAX_DURATION,
    min_lift: float = 0.08,
    rows: int = 30,
    page: int | None = None,
    export: bool = True,
    to_export_dir: bool = False,
    max_export: int = 2,
    min_free_gb: float = DEFAULT_MIN_FREE_GB,
    stopper: "Stopper | None" = None,
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
    visited: set[int] = set()
    last_page: int | None = None

    async def page_of(number: int) -> list[Lead]:
        nonlocal last_page
        found = await source.search(query, **params, rows=rows, page=number)
        total = getattr(source, "last_total", 0)
        if total:
            last_page = max(1, -(-total // rows))
        return found

    # A seam is deeper than one page, and most records have no break in them —
    # so keep turning pages until the target is met rather than giving up on
    # whatever twenty-five records happened to land first.
    leads: list[Lead] = []
    for _ in range(max_pages):
        if report.kept >= want or report.examined >= max_examine:
            break
        if chosen_page in visited:
            if last_page and len(visited) < last_page:
                chosen_page = next(
                    (n for n in range(1, last_page + 1) if n not in visited), chosen_page
                )
            else:
                break
        visited.add(chosen_page)

        leads = await page_of(chosen_page)
        if not leads and last_page and chosen_page > last_page:
            chosen_page = max(1, last_page)
            continue
        if not leads:
            break

        await _work_page(
            leads, db, client, registry, settings, report, seen,
            want=want, max_examine=max_examine, max_duration=max_duration,
            min_lift=min_lift, export=export, to_export_dir=to_export_dir,
            max_export=max_export, min_free_gb=min_free_gb,
            stopper=stopper, say=say,
        )
        if report.stopped:
            break
        # `page` is what the caller asked for and is not read again; only
        # `chosen_page` moves. Incrementing it here did nothing but crash when
        # no page was requested, which is every run from the command line.
        chosen_page = (
            chosen_page + 1 if last_page and chosen_page < last_page
            else digs_module.random_page(dig)
        )

    return report


async def _work_page(
    leads: list[Lead],
    db: Database,
    client: httpx.AsyncClient,
    registry,
    settings: Settings,
    report: HuntReport,
    seen: set[str],
    *,
    want: int,
    max_examine: int,
    max_duration: float | None,
    min_lift: float,
    export: bool,
    to_export_dir: bool,
    max_export: int,
    min_free_gb: float,
    stopper: "Stopper | None",
    say: Callable[[str], None],
) -> None:
    for lead in leads:
        if report.kept >= want or report.examined >= max_examine:
            return
        if stopper is not None and stopper.stop:
            report.stopped = "asked to stop"
            say("stopping — will pick up here next run")
            return
        # Checked per record, not once at the start: a hunt runs for a long
        # time and the disk it started on is not the disk it ends on.
        available = free_gb(settings.library_dir)
        if available < min_free_gb:
            report.stopped = (
                f"only {available:.1f} GB free, floor is {min_free_gb:.1f} GB"
            )
            say(f"stopping — {report.stopped}")
            return
        if lead.source_id in seen:
            continue
        seen.add(lead.source_id)

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
                settings, row, regions,
                to_export_dir=to_export_dir, max_export=max_export,
            )
        report.records.append(entry)
        say(f"kept {track.title[:40]} — break at {best['start_sec']:.0f}s")
