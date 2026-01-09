#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

ENV_DIR="/etc/qBitPuller"
ENV_FILE="$ENV_DIR/qBitPuller.env"
LEGACY_ENV="/etc/qBitPuller.env"

install -m 0755 "$ROOT_DIR/usr/local/bin/qBitPuller.py" /usr/local/bin/qBitPuller.py

install -d -o root -g root -m 0700 "$ENV_DIR"

if [ -f "$ENV_FILE" ]; then
  echo "Skipping $ENV_FILE (already exists)"
elif [ -f "$LEGACY_ENV" ]; then
  install -o root -g root -m 0600 "$LEGACY_ENV" "$ENV_FILE"
  echo "Migrated $LEGACY_ENV -> $ENV_FILE"
else
  install -o root -g root -m 0600 "$ROOT_DIR/etc/qBitPuller.env" "$ENV_FILE"
fi

if [ -f "$ENV_FILE" ]; then
  chown root:root "$ENV_FILE"
  chmod 0600 "$ENV_FILE"
fi

install -m 0644 "$ROOT_DIR/etc/systemd/system/qBitPuller.service" /etc/systemd/system/qBitPuller.service
install -m 0644 "$ROOT_DIR/etc/systemd/system/qBitPuller.timer" /etc/systemd/system/qBitPuller.timer

systemctl daemon-reload
systemctl enable --now qBitPuller.timer

echo "Installation terminee."
