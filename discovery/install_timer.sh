#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
USER_NAME="$(id -un)"
SERVICE="$HOME/.config/systemd/user/councilwatch-discovery.service"
TIMER="$HOME/.config/systemd/user/councilwatch-discovery.timer"
mkdir -p "$HOME/.config/systemd/user"
cat > "$SERVICE" <<EOF
[Unit]
Description=CouncilWatch five-city discovery
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$HERE
ExecStart=$HERE/run.sh
EOF
cat > "$TIMER" <<EOF
[Unit]
Description=Run CouncilWatch discovery twice daily

[Timer]
OnCalendar=*-*-* 07,19:05:00
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
EOF
echo
echo "Pi timezone:"
timedatectl show -p Timezone --value || true

# Keep the user's systemd manager alive after SSH logout so the timer
# remains genuinely unattended. This requires sudo once during setup.
sudo loginctl enable-linger "$USER_NAME"

systemctl --user daemon-reload
systemctl --user enable --now councilwatch-discovery.timer

echo
echo "Timer enabled. It runs around 7:05 AM and 7:05 PM in the Pi's local timezone."
systemctl --user list-timers councilwatch-discovery.timer --no-pager
