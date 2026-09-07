#!/bin/bash
# One hunt run, with the checks that keep it from becoming someone else's
# problem. Called by crate-hunt.service; safe to run by hand.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

DIG="${HUNT_DIG:-breaks}"
WANT="${HUNT_WANT:-8}"
EXAMINE="${HUNT_EXAMINE:-40}"
MIN_LIFT="${HUNT_MIN_LIFT:-0.08}"
MIN_FREE_GB="${HUNT_MIN_FREE_GB:-5}"
MONITOR_HEALTH="${MONITOR_HEALTH_URL:-http://127.0.0.1:8003/health}"

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

# 1. Is the neighbour well? A hunt is never worth degrading the monitor, and if
#    the monitor is already struggling this is the worst moment to add load.
if ! curl -sf --max-time 5 "$MONITOR_HEALTH" >/dev/null 2>&1; then
  log "monitor-api is not answering — skipping this run"
  exit 0
fi

# 2. Is there room? Checked here as well as inside the hunt, so a run that
#    cannot possibly help never starts.
FREE_GB=$(df -BG --output=avail "$HOME" | tail -1 | tr -dc '0-9')
if [ "${FREE_GB:-0}" -lt "$MIN_FREE_GB" ]; then
  log "only ${FREE_GB}G free, floor is ${MIN_FREE_GB}G — skipping this run"
  exit 0
fi

# 3. Is the box already busy? Load average per core; if the monitor is working
#    hard, come back in half an hour.
CORES=$(nproc 2>/dev/null || echo 1)
LOAD=$(awk '{print int($1 * 100)}' /proc/loadavg)
if [ "$LOAD" -gt $((CORES * 150)) ]; then
  log "load ${LOAD} over $CORES core(s) — too busy, skipping this run"
  exit 0
fi

log "hunting: dig=$DIG want=$WANT examine=$EXAMINE min-lift=$MIN_LIFT free=${FREE_GB}G"
exec .venv/bin/crate hunt "$DIG" \
    --want "$WANT" --max-examine "$EXAMINE" \
    --min-lift "$MIN_LIFT" --min-free-gb "$MIN_FREE_GB"
