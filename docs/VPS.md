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
.venv/bin/crate hashpw          # prints two lines
```

Paste both into `.env`:

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
