#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/freelance-ua-bot"
SERVICE_NAME="freelance-ua-bot.service"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash oracle/install_oracle.sh"
  exit 1
fi

mkdir -p "$APP_DIR"
cp freelance_ua_notifier.py "$APP_DIR/"
cp config.json "$APP_DIR/"
if [[ -f .freelance_ua_seen.json ]]; then
  cp .freelance_ua_seen.json "$APP_DIR/"
fi
cp oracle/freelance-ua-bot.service /etc/systemd/system/"$SERVICE_NAME"

chown -R opc:opc "$APP_DIR"
chmod 600 "$APP_DIR/config.json"
if [[ -f "$APP_DIR/.freelance_ua_seen.json" ]]; then
  chmod 600 "$APP_DIR/.freelance_ua_seen.json"
fi

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"
systemctl status "$SERVICE_NAME" --no-pager
