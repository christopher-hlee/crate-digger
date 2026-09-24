#!/bin/bash
# Walk every link between "the app is running" and "the URL works", and name
# the one that is broken. Run it on the box, as `platform`.
#
#   ./deploy/doctor.sh                      checks the local app + Caddy
#   ./deploy/doctor.sh https://host/crate/  also checks that URL end to end
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

PUBLIC_URL="${1:-${CRATE_PUBLIC_URL:-}}"
PORT="${CRATE_PORT:-8770}"
LOCAL="http://127.0.0.1:$PORT"
FAILED=0; WARNED=0

pass() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*"; FAILED=$((FAILED+1)); }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; WARNED=$((WARNED+1)); }
head() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fix()  { printf '      → %s\n' "$*"; }

# This runs on the VPS (systemd) and on a Mac (launchd). Checking for
# systemctl on a Mac reports "not installed" for everything and sends you
# looking for a service that was never meant to exist there.
MAC=0; [ "$(uname)" = "Darwin" ] && MAC=1
LABEL="com.cratedigger.app"

head "1. The app process"
if [ "$MAC" = 1 ]; then
  if launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
    pass "launch agent $LABEL is loaded"
  elif [ -f "$HOME/Library/LaunchAgents/$LABEL.plist" ]; then
    fail "the launch agent is installed but not loaded"
    fix "launchctl bootstrap gui/$UID ~/Library/LaunchAgents/$LABEL.plist"
  else
    warn "no launch agent — so it only runs while a terminal holds it open"
    fix "./deploy/install-local.sh   # runs at login, restarts if it dies"
  fi
elif systemctl is-active --quiet crate-api 2>/dev/null; then
  pass "crate-api is running (up $(systemctl show crate-api -p ActiveEnterTimestamp --value | cut -d' ' -f2-3))"
elif systemctl list-unit-files crate-api.service >/dev/null 2>&1; then
  fail "crate-api is installed but not running"
  fix "sudo systemctl start crate-api && journalctl -u crate-api -n 40 --no-pager"
else
  warn "crate-api is not installed as a service (running by hand?)"
  fix "./deploy/install.sh"
fi

# Answering is the authoritative signal, so ask that first: a socket listing
# that disagrees with a 200 is the listing being wrong, not the app.
HEALTH="$(curl -sf --max-time 5 "$LOCAL/api/health" 2>/dev/null)"

head "2. Is it listening?"
if [ -n "$HEALTH" ]; then
  pass "port $PORT is open — it just answered"
elif command -v ss >/dev/null || command -v netstat >/dev/null; then
  if { ss -ltn 2>/dev/null; netstat -ltn 2>/dev/null; } | grep -q ":$PORT "; then
    warn "bound to $PORT but not answering — started, then wedged or still booting"
    fix "journalctl -u crate-api -n 40 --no-pager"
  else
    fail "nothing is listening on $PORT"
    fix "journalctl -u crate-api -n 40 --no-pager   # it probably failed to boot"
  fi
else
  warn "no ss or netstat here, so this can only be judged by whether it answers"
fi

head "3. Does it answer?"
if [ -n "$HEALTH" ]; then
  pass "GET /api/health is 200"
  echo "$HEALTH" | tr ',' '\n' | sed 's/[{}"]//g' | sed 's/^/      /'
  case "$HEALTH" in
    *'"auth":false'*)
      warn "NO PASSWORD SET — anyone who reaches this can spend your disk"
      fix ".venv/bin/crate hashpw --write && sudo systemctl restart crate-api" ;;
    *) pass "a password is set" ;;
  esac
else
  fail "the app is not answering on $LOCAL"
  if [ "$MAC" = 1 ]; then
    fix "nothing is listening — that is ERR_CONNECTION_REFUSED in the browser"
    fix "./deploy/install-local.sh   # or, for now: source .venv/bin/activate && crate serve"
  else
    fix "sudo systemctl restart crate-api"
  fi
fi

head "4. Its settings"
if [ -f .env ]; then
  pass ".env exists"
  BASE_PATH="$(grep -E '^CRATE_BASE_PATH=' .env | cut -d= -f2- | tr -d '\r')"
  LIB="$(grep -E '^CRATE_LIBRARY_DIR=' .env | cut -d= -f2- | tr -d '\r')"
  LIB="${LIB:-$HOME/CrateDigger}"; LIB="${LIB/#\~/$HOME}"
  [ -n "$BASE_PATH" ] && pass "mounted under $BASE_PATH" \
    || warn "CRATE_BASE_PATH is unset — fine at the root, wrong behind a /path"
  if [ -w "$LIB" ]; then
    pass "library is writable: $LIB ($(du -sh "$LIB" 2>/dev/null | cut -f1) used, $(df -h --output=avail "$LIB" | tail -1 | tr -d ' ') free)"
  else
    fail "library is not writable: $LIB"
    fix "mkdir -p '$LIB' && check ReadWritePaths= in /etc/systemd/system/crate-api.service"
  fi
else
  if [ "$MAC" = 1 ]; then
    # launchd does not read it; the app does, from its working directory. So
    # missing just means defaults, not a failure to start.
    warn ".env is missing — running on defaults, library at ~/CrateDigger"
    fix "cp .env.example .env   # to set CRATE_EXPORT_DIR for your DAW folder"
  else
    fail ".env is missing — systemd's EnvironmentFile= makes this a start failure"
    fix "cp .env.example .env && .venv/bin/crate hashpw --write"
  fi
fi

head "5. Caddy"
if [ "$MAC" = 1 ]; then
  warn "skipped — Caddy fronts the server install, not this one"
elif systemctl is-active --quiet caddy 2>/dev/null; then
  pass "caddy is running"
  if command -v caddy >/dev/null; then
    if sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
      pass "/etc/caddy/Caddyfile is valid"
    else
      fail "/etc/caddy/Caddyfile does not parse — Caddy is serving its LAST good config"
      fix "sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile"
    fi
  fi
  if sudo grep -q "$PORT" /etc/caddy/Caddyfile 2>/dev/null; then
    pass "the Caddyfile mentions port $PORT"
  else
    fail "nothing in the Caddyfile points at 127.0.0.1:$PORT"
    fix "merge deploy/Caddyfile.snippet into your existing site block"
  fi
else
  fail "caddy is not running"
  fix "sudo systemctl status caddy"
fi

head "6. Reaching it from outside"
if [ -z "$PUBLIC_URL" ]; then
  warn "no public URL given — skipping"
  fix "./deploy/doctor.sh https://your-host/crate/"
else
  CODE="$(curl -s -o /dev/null --max-time 20 -w '%{http_code}' "$PUBLIC_URL" 2>/dev/null)"
  case "$CODE" in
    200|303|401) pass "$PUBLIC_URL answers ($CODE)" ;;
    000)
      fail "$PUBLIC_URL does not answer at all"
      PORT_OUT="$(printf '%s' "$PUBLIC_URL" | sed -nE 's#^https?://[^/:]+:([0-9]+).*#\1#p')"
      if [ -n "$PORT_OUT" ] && [ "$PORT_OUT" != "443" ] && [ "$PORT_OUT" != "80" ]; then
        fix "this URL uses port $PORT_OUT. ufw is not the only firewall:"
        fix "IONOS has a SEPARATE cloud firewall in its control panel, and it"
        fix "drops what it does not allow — which hangs instead of refusing,"
        fix "looking exactly like the app being down. Either open $PORT_OUT there,"
        fix "or serve under a path on 443, which is already open (Caddyfile.snippet)."
      else
        fix "checked from this machine, so DNS and TLS are both suspects"
      fi ;;
    404)
      fail "$PUBLIC_URL returns 404 — Caddy is answering but no route matches"
      fix "is the path right, and is handle_path before the catch-all handle?" ;;
    502|503)
      fail "$PUBLIC_URL returns $CODE — Caddy is up, the app behind it is not"
      fix "sudo systemctl restart crate-api" ;;
    *) warn "$PUBLIC_URL returns $CODE" ;;
  esac
fi

head "7. The hunt"
if [ "$MAC" = 1 ]; then
  warn "skipped — the hunt timer belongs on the box that digs"
elif systemctl list-unit-files crate-hunt.timer >/dev/null 2>&1; then
  if systemctl is-active --quiet crate-hunt.timer; then
    pass "timer is armed — next $(systemctl show crate-hunt.timer -p NextElapseUSecRealtime --value | cut -d' ' -f2-3)"
  else
    warn "crate-hunt.timer is installed but not running"
    fix "sudo systemctl enable --now crate-hunt.timer"
  fi
  LAST="$(systemctl show crate-hunt -p ExecMainStatus --value 2>/dev/null)"
  [ "${LAST:-0}" = "0" ] && pass "last hunt exited cleanly" \
    || warn "last hunt exited $LAST — journalctl -u crate-hunt -n 30 --no-pager"
else
  warn "the hunt timer is not installed"
fi

printf '\n\033[1m%s\033[0m\n' "── $FAILED broken, $WARNED worth a look ──"
[ "$FAILED" -eq 0 ] && echo "Everything the box can see is healthy." \
  || echo "Fix the ✗ items top-down: each one makes the ones below it moot."
exit $([ "$FAILED" -eq 0 ] && echo 0 || echo 1)
