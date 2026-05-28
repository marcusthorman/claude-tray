#!/usr/bin/env bash
# Set up venv, install deps, register systemd --user service.
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$PWD"

if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt

mkdir -p "$HOME/.config/systemd/user" "$HOME/.local/share/applications"
install -m 644 systemd/claude-tray.service "$HOME/.config/systemd/user/claude-tray.service"
sed "s|%h|$HOME|g" claude-tray.desktop > "$HOME/.local/share/applications/claude-tray.desktop"

systemctl --user daemon-reload
systemctl --user enable --now claude-tray.service

echo "claude-tray installed."
echo "  status:  systemctl --user status claude-tray"
echo "  stop:    systemctl --user stop claude-tray"
echo "  config:  ~/.config/claude-tray/config.toml"
