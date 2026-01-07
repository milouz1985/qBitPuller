#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

install -m 0755 "$ROOT_DIR/usr/local/bin/qBitPuller.py" /usr/local/bin/qBitPuller.py

if [ ! -f /etc/qBitPuller.env ]; then
  install -m 0600 "$ROOT_DIR/etc/qBitPuller.env" /etc/qBitPuller.env
else
  echo "Skipping /etc/qBitPuller.env (already exists)"
fi

install -m 0644 "$ROOT_DIR/etc/systemd/system/qBitPuller.service" /etc/systemd/system/qBitPuller.service
install -m 0644 "$ROOT_DIR/etc/systemd/system/qBitPuller.timer" /etc/systemd/system/qBitPuller.timer

systemctl daemon-reload
systemctl enable --now qBitPuller.timer

echo "Installation terminee."