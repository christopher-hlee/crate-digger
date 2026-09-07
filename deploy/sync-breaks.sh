#!/bin/bash
# Pull new breaks off the hunting box into a local folder — usually the one a
# DAW watches. Safe to run as often as you like: --ignore-existing means files
# already here are never re-fetched or overwritten, so a sample you have
# already chopped and renamed is left alone.
#
# Configure by env, or edit the defaults:
#   CRATE_REMOTE   user@host of the box running the hunt
#   CRATE_REMOTE_DIR  where its loops live
#   CRATE_LOCAL_DIR   where they should land here
set -uo pipefail

REMOTE="${CRATE_REMOTE:-platform@74.208.54.100}"
REMOTE_DIR="${CRATE_REMOTE_DIR:-/home/platform/CrateDigger/loops/}"
LOCAL_DIR="${CRATE_LOCAL_DIR:-$HOME/Documents/PROJECTS/samples}"
LOG="${CRATE_SYNC_LOG:-$HOME/Library/Logs/crate-sync.log}"

mkdir -p "$LOCAL_DIR" "$(dirname "$LOG")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# Check this first. Without it a missing rsync reports as the failure below,
# which sends you off checking SSH keys that were never the problem.
if ! command -v rsync >/dev/null; then
  log "rsync is not installed — brew install rsync (macOS ships an old one)"
  echo "rsync is not installed. Install it:  brew install rsync" >&2
  exit 1
fi

# BatchMode: never sit at a password prompt. Under a scheduler there is nobody
# to answer it, and the job would hang until it is killed.
BEFORE=$(find "$LOCAL_DIR" -name '*.wav' -type f 2>/dev/null | wc -l | tr -d ' ')

if ! rsync -az --ignore-existing \
        -e "ssh -o BatchMode=yes -o ConnectTimeout=10" \
        "$REMOTE:$REMOTE_DIR" "$LOCAL_DIR/" >> "$LOG" 2>&1; then
  log "rsync failed — see above. If it is asking for a password, the key is"
  log "not set up: ssh-copy-id $REMOTE, then check with: ssh $REMOTE true"
  exit 1
fi

AFTER=$(find "$LOCAL_DIR" -name '*.wav' -type f 2>/dev/null | wc -l | tr -d ' ')
NEW=$((AFTER - BEFORE))

if [ "$NEW" -gt 0 ]; then
  log "$NEW new break(s) — $AFTER in $LOCAL_DIR"
  # Only speak up when something actually arrived; a notification every
  # fifteen minutes saying "nothing" is one you learn to dismiss.
  if command -v osascript >/dev/null; then
    osascript -e "display notification \"$NEW new break$([ "$NEW" -eq 1 ] || echo s) ready\" with title \"Crate Digger\"" 2>/dev/null || true
  fi
else
  log "nothing new"
fi
