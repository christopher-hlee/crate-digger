#!/bin/bash
# Install Crate Digger on the VPS. Run as `platform`, not root.
set -euo pipefail
cd "$(dirname "$0")/.."
APP_DIR="$(pwd)"

if [ "$(id -un)" = "root" ]; then
  echo "!! Run as 'platform' — a root install leaves files the service cannot write."
  exit 1
fi

echo "==> checking the box has what it needs"
command -v python3 >/dev/null || { echo "!! python3 is not installed"; exit 1; }
if ! python3 -c "import venv" 2>/dev/null; then
  echo "!! python3-venv is missing:  sudo apt install -y python3-venv"
  exit 1
fi
command -v ffmpeg >/dev/null || echo "   (no ffmpeg — optional, but it salvages "\
"the occasional damaged Archive transfer:  sudo apt install -y ffmpeg)"

echo "==> venv"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e .

# Where records will land. Read from .env if it is already set, so a re-run
# does not point the service somewhere else.
LIBRARY_DIR="$(grep -E '^CRATE_LIBRARY_DIR=' .env 2>/dev/null | cut -d= -f2- || true)"
LIBRARY_DIR="${LIBRARY_DIR:-$HOME/CrateDigger}"
LIBRARY_DIR="${LIBRARY_DIR/#\~/$HOME}"
mkdir -p "$LIBRARY_DIR"

if [ ! -f .env ]; then
  echo "==> .env"
  cp .env.example .env
  echo
  echo "!! Set a password before this answers on a public address:"
  echo "!!     .venv/bin/crate hashpw --write"
  echo "!! Until you do, anyone who finds the URL can spend your disk and"
  echo "!! bandwidth. Check it took:  curl -s localhost:8770/api/health"
  echo "!! should say \"auth\":true."
  echo
fi

echo "==> systemd"
sudo cp deploy/crate-api.service /etc/systemd/system/
sudo cp deploy/crate-hunt.service deploy/crate-hunt.timer /etc/systemd/system/
sudo sed -i "s#^ExecStart=/usr/bin/flock -n /tmp/crate-hunt.lock .*#ExecStart=/usr/bin/flock -n /tmp/crate-hunt.lock $APP_DIR/deploy/hunt-run.sh#" \
    /etc/systemd/system/crate-hunt.service
chmod +x deploy/hunt-run.sh
# Both the working directory and the env file are baked into the units for a
# clone at /home/platform/crate-digger. Rewrite them for wherever this actually
# is, or a clone anywhere else starts against someone else's paths.
sudo sed -i \
    -e "s#^WorkingDirectory=.*#WorkingDirectory=$APP_DIR#" \
    -e "s#^EnvironmentFile=.*#EnvironmentFile=$APP_DIR/.env#" \
    -e "s#^ReadWritePaths=.*#ReadWritePaths=$APP_DIR $LIBRARY_DIR#" \
    /etc/systemd/system/crate-api.service /etc/systemd/system/crate-hunt.service
sudo sed -i "s#^ExecStart=/home/platform/crate-digger/.venv/bin/uvicorn#ExecStart=$APP_DIR/.venv/bin/uvicorn#" \
    /etc/systemd/system/crate-api.service
sudo systemctl daemon-reload
sudo systemctl enable --now crate-api.service

echo "==> health"
# `|| true` because `set -e` and a retry loop do not mix: the first failed
# probe would otherwise end the install before the service had finished booting.
for _ in $(seq 1 30); do
  if curl -sf --max-time 2 http://127.0.0.1:8770/api/health >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
if ! curl -sf --max-time 3 http://127.0.0.1:8770/api/health; then
  echo
  echo "!! The API did not come up. What went wrong:"
  echo "!!     journalctl -u crate-api -n 40 --no-pager"
  exit 1
fi
echo

cat <<DONE

Running on 127.0.0.1:8770.

  Library:               $LIBRARY_DIR
  Put it behind Caddy:   deploy/Caddyfile.snippet
  Hunt on a schedule:    sudo systemctl enable --now crate-hunt.timer
  Watch it:              journalctl -u crate-api -f
                         journalctl -u crate-hunt -f
DONE
