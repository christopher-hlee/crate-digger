#!/bin/bash
# Reach the server's Crate Digger from this machine, without putting it on the
# internet. The API binds 127.0.0.1 on the server, so a local forward is the
# whole mechanism: no Caddy block, no password, no public URL.
#
#   ./deploy/tunnel.sh            then open http://127.0.0.1:8770
#
# Configure by env:
#   CRATE_REMOTE       user@host of the box running the app
#   CRATE_TUNNEL_PORT  the port to use on THIS machine (default 8770)
#   CRATE_REMOTE_PORT  the port the app listens on THERE (default 8770)
set -uo pipefail

REMOTE="${CRATE_REMOTE:-platform@74.208.54.100}"
LOCAL_PORT="${CRATE_TUNNEL_PORT:-8770}"
REMOTE_PORT="${CRATE_REMOTE_PORT:-8770}"
URL="http://127.0.0.1:$LOCAL_PORT"

# If something already holds the port, find out what before blaming SSH. An
# already-running tunnel and a local `crate serve` both look like "port busy",
# and one of them is fine.
if curl -sf --max-time 3 "$URL/api/health" >/dev/null 2>&1; then
  echo "Already answering at $URL — nothing to do."
  echo "(That is either a tunnel you already started, or a local crate serve.)"
  exit 0
fi

command -v ssh >/dev/null || { echo "!! ssh is not installed" >&2; exit 1; }

# Only a warning: without lsof we simply do not know, and ExitOnForwardFailure
# below turns an occupied port into a clear error anyway.
if command -v lsof >/dev/null; then
  holder=$(lsof -nP -iTCP:"$LOCAL_PORT" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print $1, "pid", $2}')
  if [ -n "$holder" ]; then
    echo "!! Port $LOCAL_PORT is held by: $holder" >&2
    echo "   It is not answering as Crate Digger, so the forward would fail." >&2
    echo "   Use another port:  CRATE_TUNNEL_PORT=8771 $0" >&2
    exit 1
  fi
fi

# Under launchd there is no terminal, so an SSH passphrase prompt would hang
# forever rather than fail — and KeepAlive would restart it to hang again. With
# a terminal, leave prompts working so a passphrase key is still usable by hand.
BATCH=()
if [ ! -t 1 ]; then BATCH=(-o BatchMode=yes); fi

echo "==> tunnelling $URL  ->  $REMOTE:$REMOTE_PORT"
echo "    Leave this running. Ctrl-C closes it."
echo

# -N          no remote command, just the forward
# -T          no pseudo-terminal; nothing here is interactive
# ExitOnForwardFailure: without it, SSH connects happily while the forward
#   fails, and you get a browser that cannot connect while SSH looks fine.
# ServerAlive*: a closed laptop otherwise leaves a socket that is up but dead,
#   which hangs the browser instead of reconnecting.
exec ssh -N -T "${BATCH[@]+"${BATCH[@]}"}" \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -o ConnectTimeout=10 \
  -L "127.0.0.1:$LOCAL_PORT:127.0.0.1:$REMOTE_PORT" \
  "$REMOTE"
