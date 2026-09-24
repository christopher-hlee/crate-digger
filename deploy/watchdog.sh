#!/bin/bash
# Restart crate-api when it stops answering.
#
# systemd's Restart= only sees a process that exited. A server that is alive
# but wedged — a deadlocked worker, an exhausted thread pool — stays "active"
# forever while every request hangs. That is the failure that leaves a URL
# broken for a week, so this asks the question systemd cannot: does it answer?
set -uo pipefail

PORT="${CRATE_PORT:-8770}"
URL="http://127.0.0.1:$PORT/api/health"
STATE="${CRATE_WATCHDOG_STATE:-/tmp/crate-watchdog.strikes}"
# Two strikes, not one: a single timeout during a heavy analysis run is normal,
# and restarting for it would cut off the download in progress.
LIMIT="${CRATE_WATCHDOG_STRIKES:-2}"

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

if curl -sf --max-time 10 "$URL" >/dev/null 2>&1; then
  if [ -s "$STATE" ]; then
    log "answering again after $(cat "$STATE") missed check(s)"
    rm -f "$STATE"
  fi
  exit 0
fi

# Don't count a strike against a service that is deliberately stopped — that is
# someone doing maintenance, and restarting under them is rude and confusing.
if ! systemctl is-enabled --quiet crate-api 2>/dev/null; then
  log "not answering, but crate-api is disabled — leaving it alone"
  exit 0
fi

STRIKES=$(( $(cat "$STATE" 2>/dev/null || echo 0) + 1 ))
echo "$STRIKES" > "$STATE"
log "no answer from $URL (strike $STRIKES of $LIMIT)"

if [ "$STRIKES" -ge "$LIMIT" ]; then
  log "restarting crate-api"
  sudo systemctl restart crate-api
  rm -f "$STATE"
  sleep 10
  if curl -sf --max-time 10 "$URL" >/dev/null 2>&1; then
    log "back up"
  else
    # Say it once. A watchdog that shouts every five minutes is one you mute,
    # and a muted watchdog is worse than none.
    log "STILL DOWN after a restart — this needs a person"
    log "journalctl -u crate-api -n 50 --no-pager"
  fi
fi
