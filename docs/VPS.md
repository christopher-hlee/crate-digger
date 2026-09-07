# Running it on the VPS

You do not have to. Locally is the better default, and here is the honest
trade — then how to do it if you want it anyway.

## What you lose by moving it

**The DAW folder stops working.** `CRATE_EXPORT_DIR` writes to a folder on the
machine running the app. On the VPS that is the VPS's disk, not your Ableton
User Library, so chops no longer appear in Ableton's browser. That is the single
biggest thing local gives you.

**The library lives on the VPS.** Records download to its disk. A hunt that
keeps a few hundred records is a few GB — check what your IONOS box has spare
before you set the timer loose.

Drag-out still works: the browser hands the OS a URL, and it does not care that
the URL is remote.

## What you gain

**Hunting runs without you.** That is the real reason to do this. Break hunting
is slow — download, decode, analyse, mostly to throw the record back — and it
does not need your laptop awake. Let the VPS work the seam on a timer and pull
the results down.

## The shape that actually works

Hunt on the VPS, mix locally:

1. VPS runs `crate-hunt.timer`, building a crate of breaks.
2. Your Mac pulls the rendered breaks into the Ableton folder:

```bash
rsync -av --ignore-existing \
  platform@your-vps:/home/platform/CrateDigger/loops/ \
  /Users/christopherhlee/Documents/PROJECTS/samples/
```

Put that on a cron or a Keyboard Maestro trigger and the breaks simply turn up
in Ableton. You keep the DAW integration and get the unattended digging.

## Install

```bash
ssh platform@your-vps
git clone https://github.com/christopher-hlee/crate-digger.git
cd crate-digger
./deploy/install.sh
```

It creates the venv, installs, writes the systemd units and starts the API on
`127.0.0.1:8770`.

## Lock it before it faces the internet

**This is not optional.** Crate Digger has no password by default, because
locally there is nothing to log in to. Every endpoint can spend disk and
bandwidth — `/api/hunt` will happily download for hours. On a public URL with
no password, anyone who finds it can do that to you.

```bash
.venv/bin/crate hashpw --write   # sets it in .env directly
sudo systemctl restart crate-api
curl -s localhost:8770/api/health   # "auth":true
```

**Check that `auth` reads `true`.** If it says `false` the password did not take
— most likely the lines are still commented out, which is exactly what
`--write` exists to prevent. The app logs a warning on every start when it is
running without one.

`crate hashpw` on its own prints the two lines instead, if you would rather
paste them yourself:

```
CRATE_PASSWORD_HASH=pbkdf2_sha256$...
CRATE_SESSION_SECRET=...
CRATE_API_KEY=optional-token-for-scripts   # for curl and cron
```

Then `sudo systemctl restart crate-api`. `/api/health` stays open so the box can
probe itself; everything else needs the session cookie or a Bearer token.

## Behind Caddy

Caddy already terminates TLS for Restock on this box. Add the block in
`deploy/Caddyfile.snippet`, point a subdomain at the VPS, then:

```bash
sudo systemctl reload caddy
```

The app only ever listens on loopback — Caddy is the only thing exposed.

## It does not use Claude credits

Worth saying plainly, because it changes what needs guarding. Crate Digger
talks to archive.org and to your own disk; Restock talks to Shopify, Target,
Best Buy and Telegram. Neither imports the Anthropic SDK or holds an API key —
check `pyproject.toml` and `monitor/requirements.txt` and you will not find one.

Claude credits are spent by the sessions that *write* this code, not by the
code running. A hunt can run for a week and cost nothing but bandwidth.

What the two apps genuinely compete for is the VPS: CPU, disk and IO. Break
detection decodes audio and runs median filters over spectrograms, which is the
heaviest thing either app does — and Restock's whole value is noticing a drop
within seconds. So the hunt is fenced in, below.

## Keeping out of Restock's way

`crate-hunt.service` is capped so it cannot crowd the monitor:

| | | |
|---|---|---|
| `CPUQuota=50%` | never more than half a core | a poll is never waiting on a decode |
| `CPUWeight=20` / `Nice=15` | monitor wins every contended slice | |
| `MemoryMax=1G` | hard ceiling | no swap storm |
| `IOSchedulingClass=idle` | disk reads yield | SQLite writes stay fast |
| `flock` | one hunt at a time, ever | a slow run is never joined by the next tick |

`deploy/hunt-run.sh` then refuses to start a run at all when:

- **Restock is not answering** its health endpoint — a struggling monitor is the
  worst moment to add load,
- **the disk is below the floor** (default 5 GB) — filling it means SQLite
  cannot write and the monitor silently stops,
- **load average is over 1.5× the core count** — the box is already busy.

Each check just skips the run. The timer comes back in half an hour.

## Stopping and resuming

Nothing is ever examined twice: every keep and every throw-back is committed to
SQLite as it happens. So the hunt is resumable by construction — kill it, reboot
the box, run out of disk, and the next run picks up where it stopped rather than
starting the seam again.

Stopping is graceful. SIGTERM sets a flag that is checked *between* records, so
the one in hand finishes and commits first — `systemctl stop crate-hunt` never
leaves a half-written row or a stray `.part` file.

The disk floor is checked before every single record, not once at the start: a
hunt runs for hours, and the disk it began on is not the disk it ends on.

## The hunt timer

```bash
sudo systemctl enable --now crate-hunt.timer
journalctl -u crate-hunt -f
```

Every 30 minutes it works the seam for a few more keepers. Tune it in `.env`:

```
HUNT_DIG=breaks
HUNT_WANT=8            # keepers per run
HUNT_EXAMINE=40        # records listened to per run before giving up
HUNT_MIN_LIFT=0.08     # how far above the record's own baseline counts
```

It never re-examines a record: every keep and every throw-back is recorded, so
runs pick up where the last one stopped. Leave it a week and it will have been
through thousands.

Start with a couple of runs and listen to what lands before turning it loose —
`HUNT_MIN_LIFT` is the dial, and only your ears can set it.
