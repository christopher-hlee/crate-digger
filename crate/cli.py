"""Command line front end.

    crate serve                       start the web app
    crate dig soul-45s                rummage a seam and print what's down there
    crate digs                        list the seams
    crate search "upright bass"       search an archive directly
    crate pull <source> <id> <url>    download + analyse one thing
    crate import <path>               bring in a file you already have
    crate ls --bpm 80 100             what's in the crate
    crate chop <id> --export          slice a sample into one-shots
    crate loop <id> 12.0 19.5 --bpm 88
    crate analyze <id>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import digs as digs_module
from . import library
from .audio import chop as chopper
from .audio.cache import CACHE
from .config import Settings, get_settings
from .db import Database
from .sources.base import Lead, SourceError
from .sources.registry import Registry


def _fmt(row: dict) -> str:
    bits = [
        f"[{row.get('id', '-')}]",
        (row.get("title") or "")[:46].ljust(46),
        (row.get("artist") or "")[:22].ljust(22),
        f"{row['bpm']:6.1f}bpm" if row.get("bpm") else " " * 9,
        (row.get("musical_key") or "").ljust(9),
        f"{row['duration']:6.1f}s" if row.get("duration") else " " * 7,
        (row.get("status") or ""),
    ]
    return "  ".join(bits)


async def _dig(settings: Settings, db: Database, args) -> int:
    dig = digs_module.get(args.slug)
    if not dig:
        print(f"No dig called {args.slug!r}. Try: crate digs", file=sys.stderr)
        return 1
    registry = Registry(settings)
    try:
        source = registry.get(dig.source)
        page = args.page or digs_module.random_page(dig)
        leads = await source.search(**{**dig.params, "rows": args.rows, "page": page})
        seen = db.seen_ids(dig.source)
        leads = [l for l in leads if l.source_id not in seen]
        print(f"{dig.name} — page {page} — {len(leads)} unseen\n")
        for lead in leads:
            print(f"  {lead.source_id[:38].ljust(38)}  {lead.title[:44]}")
            if args.verbose:
                print(f"      {lead.artist} · {lead.year or '?'} · {lead.license}")
        if args.pull:
            print(f"\nPulling {min(args.pull, len(leads))}…")
            for lead in leads[: args.pull]:
                tracks = await registry.ia.tracks(lead.source_id) if dig.source == "ia" else [lead]
                for track in tracks[:1]:
                    row = await library.ingest_lead(
                        db, registry.client, track,
                        audio_dir=settings.audio_dir, max_mb=settings.max_download_mb,
                    )
                    print("  " + _fmt(row))
    except SourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        await registry.aclose()
    return 0


async def _refetch(settings: Settings, db: Database, args) -> int:
    row = db.get_sample(args.sample_id)
    if not row:
        print(f"No sample {args.sample_id}", file=sys.stderr)
        return 1
    if not row.get("stream_url"):
        print("That record has no source URL to fetch again.", file=sys.stderr)
        return 1

    registry = Registry(settings)
    try:
        lead = Lead(
            source=row["source"], source_id=row["source_id"],
            title=row.get("title") or "", stream_url=row["stream_url"],
        )
        old = Path(row["file_path"]) if row.get("file_path") else None
        if old and old.is_file():
            old.unlink()
        db.update_sample(args.sample_id, file_path=None, notes="")
        fresh = await library.ingest_lead(
            db, registry.client, lead,
            audio_dir=settings.audio_dir, max_mb=settings.max_download_mb,
        )
    finally:
        await registry.aclose()

    print(_fmt(fresh))
    if fresh.get("notes"):
        print(f"  still damaged: {fresh['notes']}", file=sys.stderr)
        print("  the copy on the Archive is probably the broken one.", file=sys.stderr)
    return 0


async def _hunt(settings: Settings, db: Database, args) -> int:
    from . import hunt as hunt_module

    dig = digs_module.get(args.slug)
    if not dig:
        print(f"No dig called {args.slug!r}. Try: crate digs", file=sys.stderr)
        return 1
    registry = Registry(settings)
    try:
        print(f"Hunting breaks in {dig.name}…\n")
        with hunt_module.Stopper() as stopper:
            report = await hunt_module.hunt(
                db, registry.client, registry, settings, dig=dig,
                want=args.want, max_examine=args.max_examine,
                max_duration=args.max_duration, min_lift=args.min_lift,
                min_free_gb=args.min_free_gb, stopper=stopper,
                export=not args.no_export,
                on_progress=lambda m: print(f"  {m}", file=sys.stderr),
            )
    except SourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        await registry.aclose()

    print(f"\n{report.kept} keeper(s) from {report.examined} listened to "
          f"({report.no_break} had no break, {report.skipped_long} too long, "
          f"{report.errors} failed)")
    if report.stopped:
        print(f"Stopped early: {report.stopped}. Nothing is lost — the next run "
              f"resumes from here.", file=sys.stderr)
    print()
    for entry in report.records:
        b = entry["break"]
        print(f"  [{entry['sample_id']}] {(entry['title'] or '')[:44].ljust(44)}"
              f"  break {b['start_sec']:6.1f}-{b['end_sec']:6.1f}s"
              f"  +{b['lift'] * 100:.0f} over the record")
        for f in entry.get("files", []):
            print(f"        {f['path']}")
    return 0


async def _search(settings: Settings, args) -> int:
    registry = Registry(settings)
    try:
        adapter = registry.get(args.source)
        kwargs = {"rows": args.rows, "page": args.page}
        if args.source == "ia":
            kwargs.update(
                year_from=args.year_from, year_to=args.year_to,
                collections=args.collections.split(",") if args.collections else None,
                subjects=args.subjects.split(",") if args.subjects else None,
            )
        leads = await adapter.search(args.query, **kwargs)
        if args.json:
            print(json.dumps([l.as_dict() for l in leads], indent=2))
        else:
            for lead in leads:
                print(f"  {lead.source_id[:38].ljust(38)}  {lead.title[:44]}  "
                      f"{lead.year or ''}")
        print(f"\n{len(leads)} results", file=sys.stderr)
    except SourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        await registry.aclose()
    return 0


async def _pull(settings: Settings, db: Database, args) -> int:
    registry = Registry(settings)
    try:
        if args.source == "ia" and not args.url:
            tracks = await registry.ia.tracks(args.source_id)
            if not tracks:
                print("No audio on that item", file=sys.stderr)
                return 1
            targets = tracks[: args.limit]
        else:
            targets = [Lead(source=args.source, source_id=args.source_id,
                            title=args.title or args.source_id, stream_url=args.url)]
        for lead in targets:
            row = await library.ingest_lead(
                db, registry.client, lead,
                audio_dir=settings.audio_dir, max_mb=settings.max_download_mb,
            )
            print(_fmt(row))
            if row.get("error"):
                print(f"  ! {row['error']}", file=sys.stderr)
    except SourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        await registry.aclose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="crate", description="Crate Digger — a sample crate you can dig through."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="run the web app")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--reload", action="store_true")

    sub.add_parser("digs", help="list the preset digs")
    p = sub.add_parser("hashpw", help="set the password that guards a served install")
    p.add_argument("--write", action="store_true",
                   help="write straight into .env instead of printing")
    p.add_argument("--env", default=".env", help="which file to write")

    p = sub.add_parser("dig", help="rummage through one seam")
    p.add_argument("slug")
    p.add_argument("--page", type=int, default=None, help="default: a random page")
    p.add_argument("--rows", type=int, default=25)
    p.add_argument("--pull", type=int, default=0, metavar="N", help="download the first N")
    p.add_argument("-v", "--verbose", action="store_true")

    p = sub.add_parser("search", help="search an archive")
    p.add_argument("query")
    p.add_argument("--source", default="ia", choices=["ia", "openverse", "discogs"])
    p.add_argument("--rows", type=int, default=25)
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--year-from", type=int, dest="year_from")
    p.add_argument("--year-to", type=int, dest="year_to")
    p.add_argument("--collections")
    p.add_argument("--subjects")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("pull", help="download and analyse one record")
    p.add_argument("source")
    p.add_argument("source_id")
    p.add_argument("url", nargs="?", default="")
    p.add_argument("--title", default="")
    p.add_argument("--limit", type=int, default=3, help="tracks per archive item")

    p = sub.add_parser("import", help="bring in a local file")
    p.add_argument("path")
    p.add_argument("--no-copy", action="store_true")

    p = sub.add_parser("ls", help="list the crate")
    p.add_argument("--bpm", nargs=2, type=float, metavar=("MIN", "MAX"))
    p.add_argument("--key")
    p.add_argument("--breaks", type=float, metavar="MIN", help="0-1 percussive ratio")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("refetch", help="download a record again (a damaged transfer)")
    p.add_argument("sample_id", type=int)

    p = sub.add_parser("rescan", help="re-run analysis over everything already downloaded")
    p.add_argument("--breaks-only", action="store_true", dest="breaks_only",
                   help="only redo break detection, keep tempo and key")
    p.add_argument("--limit", type=int, default=0, help="stop after N records")

    p = sub.add_parser("analyze", help="re-analyse a sample")
    p.add_argument("sample_id", type=int)

    p = sub.add_parser("chop", help="slice a sample into one-shots")
    p.add_argument("sample_id", type=int)
    p.add_argument("--sensitivity", type=float, default=1.0)
    p.add_argument("--grid", type=float, default=None, metavar="BEATS")
    p.add_argument("--export", action="store_true", help="write WAVs")

    p = sub.add_parser("hunt", help="pull records from a seam, keep only ones with breaks")
    p.add_argument("slug", nargs="?", default="breaks")
    p.add_argument("--want", type=int, default=6, help="how many keepers to find")
    p.add_argument("--max-examine", type=int, default=30, dest="max_examine")
    p.add_argument("--max-duration", type=float, default=720.0, dest="max_duration")
    p.add_argument("--min-lift", type=float, default=0.08, dest="min_lift")
    p.add_argument("--min-free-gb", type=float, default=5.0, dest="min_free_gb",
                   help="stop when the disk gets this low")
    p.add_argument("--no-export", action="store_true")

    p = sub.add_parser("breaks", help="find the drum-only stretches in a record")
    p.add_argument("sample_id", type=int)
    p.add_argument("--export", action="store_true", help="write each one as a WAV")

    p = sub.add_parser("loop", help="render a loop, optionally varispeeded")
    p.add_argument("sample_id", type=int)
    p.add_argument("start", type=float)
    p.add_argument("end", type=float)
    p.add_argument("--bpm", type=float, default=None, help="target tempo")

    args = parser.parse_args(argv)
    settings = get_settings()
    db = Database(settings.db_path)

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run(
            "crate.server.app:app",
            host=args.host or settings.host,
            port=args.port or settings.port,
            reload=args.reload,
        )
        return 0

    if args.cmd == "hashpw":
        import getpass

        from .security import hash_password, random_secret

        pw = getpass.getpass("Password: ")
        if pw != getpass.getpass("Again: "):
            print("They do not match.", file=sys.stderr)
            return 1
        values = {
            "CRATE_PASSWORD_HASH": hash_password(pw),
            "CRATE_SESSION_SECRET": random_secret(),
        }

        if not args.write:
            print("\nPut these in your .env — uncommented:\n")
            for key, value in values.items():
                print(f"{key}={value}")
            print("\nOr skip the copy-paste:  crate hashpw --write")
            return 0

        # Writing it is the whole point: a hash pasted back in as a comment
        # leaves the app open, and the only sign is `auth: false` in /health.
        env_path = Path(args.env)
        lines = env_path.read_text().splitlines() if env_path.is_file() else []
        for key, value in values.items():
            replacement = f"{key}={value}"
            for i, line in enumerate(lines):
                if line.lstrip("# ").startswith(f"{key}="):
                    lines[i] = replacement
                    break
            else:
                lines.append(replacement)
        env_path.write_text("\n".join(lines) + "\n")
        print(f"Wrote CRATE_PASSWORD_HASH and CRATE_SESSION_SECRET to {env_path}")
        print("Restart for it to take effect:  sudo systemctl restart crate-api")
        return 0

    if args.cmd == "digs":
        for dig in digs_module.DIGS:
            print(f"  {dig.slug.ljust(24)} {dig.name.ljust(26)} {dig.blurb}")
        return 0

    if args.cmd == "refetch":
        return asyncio.run(_refetch(settings, db, args))

    if args.cmd == "rescan":
        from .audio import dsp

        rows = db.query(
            "SELECT * FROM samples WHERE file_path IS NOT NULL ORDER BY id"
        )
        if args.limit:
            rows = rows[: args.limit]
        changed = lost = gained = 0
        for row in rows:
            path = Path(row["file_path"])
            if not path.is_file():
                print(f"  [{row['id']}] missing on disk: {path}", file=sys.stderr)
                continue
            had = bool(row.get("breaks") and '"usable": true' in (row["breaks"] or ""))
            try:
                if args.breaks_only:
                    y22, _ = CACHE.load(path, sr=22050)
                    found = dsp.find_breaks(y22, 22050)
                    db.update_sample(row["id"], breaks=found)
                else:
                    db.update_sample(
                        row["id"], **library.analyze_file(path), status="ready"
                    )
                    found = db.get_sample(row["id"])["breaks"] or []
            except Exception as exc:
                print(f"  [{row['id']}] failed: {exc}", file=sys.stderr)
                continue

            now = dsp.has_usable_break(found)
            changed += 1
            mark = "  "
            if had and not now:
                lost += 1
                mark = "- "
            elif now and not had:
                gained += 1
                mark = "+ "
            title = (row.get("title") or "")[:46].ljust(46)
            if row.get("notes", "").startswith("Damaged transfer"):
                mark = "! "
            detail = "no break" if not now else (
                f"break {found[0]['start_sec']:6.1f}-{found[0]['end_sec']:6.1f}s"
                f"  +{found[0]['lift'] * 100:.0f}")
            print(f"{mark}[{row['id']:4d}] {title} {detail}")

        print(f"\n{changed} record(s) re-read · {lost} lost a break it never had"
              f" · {gained} gained one", file=sys.stderr)
        damaged = [r["id"] for r in rows
                   if (r.get("notes") or "").startswith("Damaged transfer")]
        if damaged:
            print(f"! {len(damaged)} damaged transfer(s): "
                  f"{', '.join(str(i) for i in damaged)} — try `crate refetch <id>`",
                  file=sys.stderr)
        if lost:
            print("Old renders in loops/ are stale — clear them and export again.",
                  file=sys.stderr)
        return 0

    if args.cmd == "hunt":
        return asyncio.run(_hunt(settings, db, args))
    if args.cmd == "dig":
        return asyncio.run(_dig(settings, db, args))
    if args.cmd == "search":
        return asyncio.run(_search(settings, args))
    if args.cmd == "pull":
        return asyncio.run(_pull(settings, db, args))

    if args.cmd == "import":
        row = library.import_local(
            db, args.path, audio_dir=settings.audio_dir, copy=not args.no_copy
        )
        print(_fmt(row))
        return 0

    if args.cmd == "ls":
        where, params = [], []
        if args.bpm:
            where.append("bpm BETWEEN ? AND ?")
            params += args.bpm
        if args.key:
            where.append("musical_key = ?")
            params.append(args.key)
        if args.breaks is not None:
            where.append("breakiness >= ?")
            params.append(args.breaks)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = db.query(
            f"SELECT * FROM samples{clause} ORDER BY created_at DESC LIMIT ?",
            [*params, args.limit],
        )
        for row in rows:
            print(_fmt(row))
        print(f"\n{len(rows)} of {db.one('SELECT COUNT(*) n FROM samples')['n']}",
              file=sys.stderr)
        return 0

    row = db.get_sample(args.sample_id)
    if not row:
        print(f"No sample {args.sample_id}", file=sys.stderr)
        return 1
    if not row.get("file_path") or not Path(row["file_path"]).is_file():
        print(f"Sample {args.sample_id} has no file on disk", file=sys.stderr)
        return 1
    path = Path(row["file_path"])

    if args.cmd == "analyze":
        db.update_sample(args.sample_id, **library.analyze_file(path), status="ready")
        print(_fmt(db.get_sample(args.sample_id) or {}))
        return 0

    y, sr = CACHE.load(path)

    if args.cmd == "chop":
        if args.grid:
            if not row.get("bpm"):
                print("Grid chopping needs a BPM — run `crate analyze` first", file=sys.stderr)
                return 1
            slices = chopper.chop_grid(y, sr, row["bpm"], division=args.grid)
        else:
            slices = chopper.chop_transient(y, sr, sensitivity=args.sensitivity)
        stem = library.slugify(row.get("title") or f"sample-{args.sample_id}")
        out_dir = settings.slices_dir / f"{args.sample_id:05d}-{stem}"
        for s in slices:
            line = f"  {s.idx:3d}  {s.start:8.3f} → {s.end:8.3f}  ({s.length:.3f}s)"
            if args.export:
                target = chopper.write_wav(
                    out_dir / f"{stem}-{s.idx:02d}.wav",
                    chopper.take(y, sr, s.start, s.end), sr,
                )
                line += f"  {target}"
            print(line)
        print(f"\n{len(slices)} slices" + (f" → {out_dir}" if args.export else ""),
              file=sys.stderr)
        return 0

    if args.cmd == "breaks":
        from .audio import dsp

        y22, _ = decode_for_analysis = CACHE.load(path, sr=22050)
        found = dsp.find_breaks(y22, 22050)
        stem = library.slugify(row.get("title") or f"sample-{args.sample_id}")
        for i, region in enumerate(found):
            line = (f"  {i + 1:2d}  {region['start_sec']:8.2f} → {region['end_sec']:8.2f}"
                    f"  ({region['length_sec']:6.2f}s)  {region['score'] * 100:5.1f}% drums"
                    f"  +{region['lift'] * 100:.1f} over this record")
            if args.export:
                target = chopper.write_wav(
                    settings.loops_dir / f"{stem}-break-{i + 1:02d}.wav",
                    chopper.take(y, sr, region["start_sec"], region["end_sec"]), sr)
                line += f"\n      {target}"
            print(line)
        if not found:
            print("  no exposed drums found — this one plays all the way through",
                  file=sys.stderr)
        return 0

    if args.cmd == "loop":
        seg, info = chopper.render_loop(
            y, sr, args.start, args.end,
            source_bpm=row.get("bpm"), target_bpm=args.bpm,
        )
        stem = library.slugify(row.get("title") or f"sample-{args.sample_id}")
        target = chopper.write_wav(
            settings.loops_dir / f"{stem}-{int(args.start)}s.wav", seg, sr
        )
        print(target)
        print(f"  {info['bars'] or '?'} bars · speed {info['speed']} · "
              f"{info['semitones']:+.2f} semitones", file=sys.stderr)
        return 0

    return 0
