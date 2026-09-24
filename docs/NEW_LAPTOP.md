# Setting up on a new laptop

Start to finish on a Mac that has never seen this before. Roughly ten minutes,
most of it waiting for `pip`.

If you only want a working app and don't care about the records you already
have, steps 1–3 are the whole thing.

---

## 1. Prerequisites

macOS ships Python 3.9, and this needs 3.10+. Check before anything else:

```bash
python3 --version
```

If that says 3.9 or the command is missing:

```bash
xcode-select --install          # git and a compiler, if you don't have them
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install python@3.12
```

Nothing else is required. There is no ffmpeg dependency — libsndfile reads
MP3, FLAC, OGG and WAV. (ffmpeg is used only as a fallback for odd containers;
`brew install ffmpeg` if you ever see a decode warning, but don't bother up
front.)

## 2. Install

```bash
git clone https://github.com/christopher-hlee/crate-digger.git
cd crate-digger
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## 3. Run it

```bash
crate serve
# → http://127.0.0.1:8770
```

That lives as long as the terminal does. To have it always there — at login,
and again if it ever crashes:

```bash
./deploy/install-local.sh
```

```
Logs:     tail -f ~/Library/Logs/crate-digger.log
Restart:  launchctl kickstart -k gui/$UID/com.cratedigger.app
Stop:     launchctl bootout gui/$UID/com.cratedigger.app
```

At this point you have a working, empty crate. Everything below is about
filling it.

## 4. Point it at your DAW

```bash
cp .env.example .env
```

Edit two lines:

```ini
CRATE_LIBRARY_DIR=~/CrateDigger
CRATE_EXPORT_DIR=~/Documents/PROJECTS/samples
```

`CRATE_EXPORT_DIR` should be a folder Ableton's browser already watches (add
it under **Places** if not). Breaks you add land there as WAVs, and show up in
Ableton without an import step.

Restart after editing `.env` — it is read once at startup.

---

## 5. Bringing your existing crate across

Skip this on a genuinely fresh start.

Your crate is one folder — records, renders and the database together:

```bash
# on the old laptop
rsync -av --progress ~/CrateDigger/ newlaptop.local:~/CrateDigger/
```

An external drive works as well; it is just a folder.

**Then run this on the new laptop:**

```bash
crate relocate
```

This matters. The database stores absolute paths, so a crate copied to a
different machine — or merely a different macOS username — refers to files
that are not there, and every record comes up dead. `relocate` repoints them
at wherever the library now lives. It is safe to run twice and
`--dry-run` shows what it would change without writing.

```
  0 already correct · 341 repointed · 0 missing on disk · 0 unplaceable
```

`missing on disk` means the database knows a record whose audio didn't come
across — `crate refetch <id>` downloads it again.

## 6. Reconnecting the VPS hunt

The server keeps hunting whether or not this laptop is on. What it finds waits
in `picked/` until you pull it down.

```bash
ssh-keygen -t ed25519                       # if this laptop has no key yet
ssh-copy-id platform@74.208.54.100
ssh platform@74.208.54.100 true             # must not prompt for a password
```

That last line has to succeed silently — the sync runs unattended and will
never sit at a password prompt.

```bash
./deploy/sync-breaks.sh                     # pull now
cp deploy/com.cratedigger.sync.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.cratedigger.sync.plist
```

It only ever copies down; `--ignore-existing` means a sample you already
chopped and renamed is never overwritten.

## 7. Check it

```bash
./deploy/doctor.sh
```

Walks the chain and names the broken link rather than making you guess. It
knows the difference between this Mac and the server.

---

## If something is wrong

| What you see | What it is |
|---|---|
| `crate: command not found` | The venv isn't active — `source .venv/bin/activate` |
| Records listed but nothing plays | Paths from the old machine — `crate relocate` |
| `Path is outside the library` | Same thing; `relocate` fixes it |
| Breaks shelf empty | Nothing analysed yet — `crate hunt breaks --want 6` |
| Port 8770 refuses | Not running — `tail ~/Library/Logs/crate-digger.log` |
| `rsync` asks for a password | The SSH key isn't on the server; redo step 6 |

More: [`DAW_WORKFLOW.md`](DAW_WORKFLOW.md) for wiring into Ableton, FL, Logic
and Maschine · [`SOURCES.md`](SOURCES.md) for where audio comes from ·
[`VPS.md`](VPS.md) for the server side.
