#!/bin/bash
# Install Crate Digger on the VPS. Run as `platform`, not root.
set -euo pipefail
cd "$(dirname "$0")/.."
APP_DIR="$(pwd)"

if [ "$(id -un)" = "root" ]; then
  echo "!! Run as 'platform' — a root install leaves files the service cannot write."
  exit 1
fi

echo "==> venv"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e .

if [ ! -f .env ]; then
  echo "==> .env"
  cp .env.example .env
  echo
  echo "!! Set a password before this answers on a public address:"
  echo "!!     .venv/bin/crate hashpw"
  echo "!! and paste both lines into .env. Until you do, anyone who finds the"
  echo "!! URL can spend your disk and bandwidth."
  echo
fi

echo "==> systemd"
sudo cp deploy/crate-api.service /etc/systemd/system/
sudo cp deploy/crate-hunt.service deploy/crate-hunt.timer /etc/systemd/system/
sudo sed -i "s#^ExecStart=/usr/bin/flock -n /tmp/crate-hunt.lock .*#ExecStart=/usr/bin/flock -n /tmp/crate-hunt.lock $APP_DIR/deploy/hunt-run.sh#" \
    /etc/systemd/system/crate-hunt.service
chmod +x deploy/hunt-run.sh
sudo sed -i "s#^WorkingDirectory=.*#WorkingDirectory=$APP_DIR#" \
    /etc/systemd/system/crate-api.service /etc/systemd/system/crate-hunt.service
sudo systemctl daemon-reload
sudo systemctl enable --now crate-api.service

echo "==> health"
for _ in $(seq 1 20); do
  curl -sf --max-time 2 http://127.0.0.1:8770/api/health >/dev/null && break
  sleep 1
done
curl -s http://127.0.0.1:8770/api/health; echo

cat <<'DONE'

Running on 127.0.0.1:8770.

  Put it behind Caddy:   deploy/Caddyfile.snippet
  Hunt on a schedule:    sudo systemctl enable --now crate-hunt.timer
  Watch it:              journalctl -u crate-api -f
                         journalctl -u crate-hunt -f
DONE
