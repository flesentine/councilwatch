#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.config/systemd/user"

cat > "$HOME/.config/systemd/user/councilwatch-review.service" <<SERVICE
[Unit]
Description=CouncilWatch private draft review web app
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$HERE
Environment=PYTHONUTF8=1
Environment=PYTHONIOENCODING=utf-8
ExecStart=$HERE/.venv/bin/uvicorn review_app:app --host 0.0.0.0 --port 8080
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
SERVICE

cat > "$HOME/.config/systemd/user/councilwatch-generate-five.service" <<SERVICE
[Unit]
Description=Generate five private CouncilWatch test stories
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$HERE
Environment=PYTHONUTF8=1
Environment=PYTHONIOENCODING=utf-8
ExecStart=$HERE/.venv/bin/python $HERE/generate_five.py
SERVICE

systemctl --user daemon-reload
systemctl --user enable --now councilwatch-review.service

echo
echo "Review server started:"
echo "  http://raspberrypi.local:8080"
