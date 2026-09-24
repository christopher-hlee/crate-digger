#!/bin/bash
# Keep Crate Digger running on your own Mac — at login, and after it crashes.
#
# `crate serve` in a terminal lasts exactly as long as the terminal. This
# installs it as a launch agent instead, so http://127.0.0.1:8770 is simply
# always there.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
LABEL="com.cratedigger.app"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

[ "$(uname)" = "Darwin" ] || { echo "!! macOS only — the VPS uses systemd (deploy/install.sh)"; exit 1; }
[ -x "$REPO/.venv/bin/crate" ] || {
  echo "!! No venv here. Build it first:"
  echo "     python3 -m venv .venv && .venv/bin/pip install -e ."
  exit 1
}

echo "==> writing $PLIST"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
sed -e "s#__REPO__#$REPO#g" -e "s#__HOME__#$HOME#g" \
    deploy/com.cratedigger.app.plist > "$PLIST"

# bootout first so a re-run reloads rather than erroring on a duplicate label.
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST"
launchctl enable "gui/$UID/$LABEL"

echo "==> waiting for it to answer"
for _ in $(seq 1 30); do
  if curl -sf --max-time 2 http://127.0.0.1:8770/api/health >/dev/null 2>&1; then
    echo
    curl -s http://127.0.0.1:8770/api/health; echo
    cat <<DONE

  Running at http://127.0.0.1:8770 — and it will be after a reboot too.

    Logs:     tail -f ~/Library/Logs/crate-digger.log
    Restart:  launchctl kickstart -k gui/$UID/$LABEL
    Stop:     launchctl bootout gui/$UID/$LABEL
DONE
    exit 0
  fi
  sleep 1
done

echo "!! It did not come up. What went wrong:"
echo "     tail -30 ~/Library/Logs/crate-digger.log"
exit 1
