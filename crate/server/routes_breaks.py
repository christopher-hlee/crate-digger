"""Browsing every break in the crate, and choosing the ones you want.

The hunt fills a shelf; this is how you stand in front of it. Breaks live on
whichever machine did the digging until you pick one — picking is what puts a
file somewhere worth syncing.
"""
from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from .. import library
from ..audio import chop as chopper
from ..audio.cache import CACHE
from ..db import hydrate_all
from .deps import require_audio, state

router = APIRouter(prefix="/api", tags=["breaks"])


class PickIn(BaseModel):
    note: str = ""


def _rows_to_breaks(rows: list[dict], *, usable_only: bool) -> list[dict]:
    """Flatten per-record break lists into one browsable shelf."""
    out: list[dict] = []
    for row in rows:
        for idx, region in enumerate(row.get("breaks") or []):
            if usable_only and not region.get("usable"):
                continue
            out.append(
                {
                    **region,
                    "idx": idx,
                    "sample_id": row["id"],
                    "title": row.get("title"),
                    "artist": row.get("artist"),
                    "year": row.get("year"),
                    "bpm": row.get("bpm"),
                    "musical_key": row.get("musical_key"),
                    "license": row.get("license"),
                    "page_url": row.get("page_url"),
                    # Audition streams the region out of the source file; no
                    # WAV is written until you pick it.
                    "audition_url": (
                        f"/api/samples/{row['id']}/file"
                        f"#t={region['start_sec']:.2f},{region['end_sec']:.2f}"
                    ),
                }
            )
    return out


@router.get("/breaks")
async def list_breaks(
    request: Request,
    bpm_min: float | None = None,
    bpm_max: float | None = None,
    picked: bool | None = None,
    usable_only: bool = True,
    sort: str = "score",
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict:
    """Every break in the crate, newest records first, best breaks on top."""
    db = state(request).db
    where = ["s.breaks IS NOT NULL", "s.file_path IS NOT NULL"]
    params: list = []
    if bpm_min is not None:
        where.append("s.bpm >= ?")
        params.append(bpm_min)
    if bpm_max is not None:
        where.append("s.bpm <= ?")
        params.append(bpm_max)

    rows = hydrate_all(
        db.query(
            f"SELECT s.* FROM samples s WHERE {' AND '.join(where)}"
            " ORDER BY s.created_at DESC",
            params,
        )
    )
    breaks = _rows_to_breaks(rows, usable_only=usable_only)

    chosen = {
        (p["sample_id"], p["idx"]) for p in db.query("SELECT sample_id, idx FROM picks")
    }
    for entry in breaks:
        entry["picked"] = (entry["sample_id"], entry["idx"]) in chosen
    if picked is not None:
        breaks = [b for b in breaks if b["picked"] is picked]

    keys = {
        "score": lambda b: -b.get("score", 0),
        "lift": lambda b: -b.get("lift", 0),
        "length": lambda b: -b.get("length_sec", 0),
        "bpm": lambda b: (b.get("bpm") is None, b.get("bpm") or 0),
    }
    breaks.sort(key=keys.get(sort, keys["score"]))
    return {"count": len(breaks), "breaks": breaks[:limit]}


@router.post("/breaks/{sample_id}/{idx}/pick")
async def pick_break(
    request: Request, sample_id: int, idx: int, body: PickIn | None = None
) -> dict:
    """Choose a break: render it to `picked/`, which is the folder you sync."""
    app_state = state(request)
    row, path = require_audio(request, sample_id)
    regions = row.get("breaks") or []
    if idx >= len(regions):
        raise HTTPException(404, f"Record {sample_id} has no break {idx}")
    region = regions[idx]
    settings = app_state.settings

    def render() -> Path:
        y, sr = CACHE.load(path)
        stem = library.slugify(
            " ".join(x for x in (row.get("artist"), row.get("title")) if x) or "break"
        )
        bpm = f"-{round(row['bpm'])}bpm" if row.get("bpm") else ""
        name = f"{stem}{bpm}-break-{idx + 1:02d}.wav"
        seg = chopper.take(y, sr, region["start_sec"], region["end_sec"])
        target = chopper.write_wav(settings.picked_dir / name, seg, sr)
        if settings.export_dir:
            shutil.copy2(target, Path(settings.export_dir) / name)
        return target

    target = await asyncio.to_thread(render)
    app_state.db.execute(
        "INSERT OR REPLACE INTO picks"
        "(sample_id, idx, start_sec, end_sec, file_path, note, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (sample_id, idx, region["start_sec"], region["end_sec"], str(target),
         (body.note if body else ""), time.time()),
    )
    return {
        "picked": True,
        "filename": target.name,
        "url": f"/api/picked/{quote(target.name)}",
        "path": str(target),
    }


@router.delete("/breaks/{sample_id}/{idx}/pick")
async def unpick_break(request: Request, sample_id: int, idx: int) -> dict:
    app_state = state(request)
    row = app_state.db.one(
        "SELECT * FROM picks WHERE sample_id=? AND idx=?", (sample_id, idx)
    )
    if row and row.get("file_path"):
        candidate = Path(row["file_path"])
        try:
            inside = candidate.resolve().is_relative_to(
                app_state.settings.picked_dir.resolve()
            )
        except OSError:
            inside = False
        if inside and candidate.is_file():
            candidate.unlink()
    app_state.db.execute(
        "DELETE FROM picks WHERE sample_id=? AND idx=?", (sample_id, idx)
    )
    return {"picked": False}


@router.get("/picked/{filename}")
async def picked_file(request: Request, filename: str):
    from .routes_audio import _serve

    settings = state(request).settings
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(400, "Bad filename")
    path = settings.picked_dir / filename
    if not path.is_file():
        raise HTTPException(404, f"No pick called {filename}")
    return _serve(path, filename, attachment=True)
