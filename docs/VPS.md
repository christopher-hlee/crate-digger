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
2. **You browse the shelf in the app and keep what you want.** Open the VPS's
   web UI, go to **Breaks**, and audition them — playback streams the region
   out of the source record, so nothing is written and nothing is downloaded.
   Pressing **Keep** renders that break into `picked/`.

3. Your Mac pulls down `picked/` — only what you chose.

Nothing syncs on its own — `scp` and `rsync` are one-shot copies, and running
one by hand forever is not a workflow. `deploy/sync-breaks.sh` does the pull;
a launch agent runs it every fifteen minutes.

**First, keys.** Under a scheduler there is nobody to type a password, so the
sync must be able to connect without one:

```bash
ssh-keygen -t ed25519            # skip if you already have one
ssh-copy-id platform@your-vps
ssh platform@your-vps true       # must return silently, no prompt
```

**Then the agent**, on your Mac:

```bash
cp deploy/com.cratedigger.sync.plist ~/Library/LaunchAgents/
# edit it: replace /Users/YOU with your home, and set the remote host
launchctl load ~/Library/LaunchAgents/com.cratedigger.sync.plist
```

It syncs `picked/`, not `loops/` — the shelf stays on the server and only the
breaks you kept come down. It runs at login and every fifteen minutes after,
pulls only files you do not already have, and posts a notification when something arrives — silently when
nothing does, because an alert every quarter hour saying "nothing" is one you
learn to swipe away.

```bash
tail -f ~/Library/Logs/crate-sync.log       # what it has been doing
deploy/sync-breaks.sh                       # or just pull right now
launchctl unload ~/Library/LaunchAgents/com.cratedigger.sync.plist   # stop
```

`--ignore-existing` is deliberate: a break you have already chopped, renamed or
edited is never overwritten by the copy still sitting on the server.

**If it never fires**, macOS may be blocking writes into `~/Documents`. Either
grant Full Disk Access to `/bin/bash` under System Settings → Privacy, or point
`CRATE_LOCAL_DIR` somewhere unprotected like `~/Music/Crate` and add that folder
to Ableton's browser instead.

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

## Reaching it from another machine without exposing it

If you only want to browse and add samples from a second machine, an SSH
tunnel needs no Caddy block, no password and no public URL:

```bash
ssh -N -L 8770:127.0.0.1:8770 platform@74.208.54.100
```

Leave that running and open `http://127.0.0.1:8770` on the laptop. The app is
on the server, so what you add lands in the server's `picked/` and your main
machine pulls it down with `deploy/sync-breaks.sh` exactly as before.

The service binds `127.0.0.1`, so this is the whole of it — nothing is
listening on a public interface either way. Worth knowing: this is also the
fastest way to check whether a problem is the app or the proxy in front of it.

Caddy is still the better answer if you want it from a phone, or from a machine
without your SSH key. Then read on — and set the password first.

## Behind Caddy

**Use a path on port 443, not a separate port.** 443 is already open, already
has a certificate, and is already proving it works by serving Restock. A second
port needs a hole in *two* firewalls: `ufw`, and the IONOS cloud firewall — a
separate policy in the IONOS control panel that `ufw` cannot see or change.
Miss the second and connections hang rather than refuse, which looks exactly
like the app being down.

Merge the block from `deploy/Caddyfile.snippet` into the site you already have
— one block per hostname, or Caddy rejects the config — then:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
```

Tell the app where it is mounted, so its redirects point back into it:

```bash
echo 'CRATE_BASE_PATH=/crate' >> .env
sudo systemctl restart crate-api
```

It is then at `https://<your-host>/crate/`. The browser works its own base out
from the script's URL, so nothing else needs configuring, and the app accepts
both `handle_path` (prefix stripped) and `handle` (prefix left on).

**Set the password before any of this.** `/api/hunt` will download for hours
for anyone who asks:

```bash
.venv/bin/crate hashpw --write
sudo systemctl restart crate-api
```

## When the URL does not work

```bash
./deploy/doctor.sh https://your-host/crate/
```

It walks every link — service, socket, health, `.env`, library permissions,
disk, Caddy's config validity, and the URL itself from the box — and names the
one that is broken along with the command that fixes it. Fix the ✗ items
top-down; each makes the ones below it moot.

The case worth knowing: if the app answers on `127.0.0.1:8770` but the public
URL does not answer at all, nothing on the box is wrong. That is a firewall
above the OS, and on IONOS that means the control panel.

## Staying up

`crate-api` restarts on any exit and never gives up (`Restart=always`,
`StartLimitIntervalSec=0` — the default quits after five restarts in ten
seconds and leaves the unit dead).

That still cannot see a server that is alive but wedged, so `crate-watchdog`
asks the question systemd cannot — does it answer? — every five minutes, and
restarts it after two consecutive misses. Two, not one: a single timeout during
a heavy analysis run is normal, and restarting for it would cut off a download
in progress. It says "still down" exactly once rather than every five minutes,
because a watchdog you mute is worse than none.

```bash
sudo systemctl enable --now crate-watchdog.timer
journalctl -u crate-watchdog -f
```

## No URL? Use a tunnel

If you would rather not expose it at all, forward the port over SSH. **Run this
on your Mac**, not on the VPS:

```bash
ssh -L 8770:127.0.0.1:8770 platform@your-vps
```

Leave that open and the VPS's app answers at `http://127.0.0.1:8770` in your own
browser. Nothing is exposed and no password is needed, because nothing is
listening publicly.

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
