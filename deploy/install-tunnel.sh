#!/bin/bash
# Keep the tunnel to the server up on this Mac — at login, and after it drops.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
LABEL="com.cratedigger.tunnel"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

REMOTE="${CRATE_REMOTE:-platform@74.208.54.100}"
PORT="${CRATE_TUNNEL_PORT:-8770}"

[ "$(uname)" = "Darwin" ] || { echo "!! macOS only"; exit 1; }

# Unattended SSH cannot answer anything. Prove the key works before installing
# an agent that would otherwise fail silently every 15 seconds.
echo "==> checking $REMOTE accepts your key without a prompt"
if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE" true 2>/dev/null; then
  cat >&2 <<MSG
!! $REMOTE asked for a password, or refused.

   The tunnel runs unattended, so there is nobody to answer a prompt. Put this
   machine's key on the server first:

     ssh-keygen -t ed25519          # only if you have no key yet
     ssh-copy-id $REMOTE
     ssh -o BatchMode=yes $REMOTE true    # must succeed silently
MSG
  exit 1
fi
echo "    key accepted"

echo "==> writing $PLIST"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
sed -e "s#__REPO__#$REPO#g" -e "s#__HOME__#$HOME#g" \
    -e "s#__REMOTE__#$REMOTE#g" -e "s#__PORT__#$PORT#g" \
    deploy/com.cratedigger.tunnel.plist > "$PLIST"

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST"
launchctl enable "gui/$UID/$LABEL"

echo "==> waiting for it to answer"
for _ in $(seq 1 30); do
  if curl -sf --max-time 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    echo
    curl -s "http://127.0.0.1:$PORT/api/health"; echo
    cat <<DONE

  The server's crate is at http://127.0.0.1:$PORT — and will be after a reboot.

    Logs:     tail -f ~/Library/Logs/crate-tunnel.log
    Restart:  launchctl kickstart -k gui/$UID/$LABEL
    Stop:     launchctl bootout gui/$UID/$LABEL
DONE
    exit 0
  fi
  sleep 1
done

echo "!! No answer on port $PORT. What went wrong:"
echo "     tail -30 ~/Library/Logs/crate-tunnel.log"
echo "   If SSH is fine, check the app is up on the server:"
echo "     ssh $REMOTE 'curl -s localhost:8770/api/health'"
exit 1
