#!/usr/bin/env bash
set -euo pipefail
systemctl --user reset-failed councilwatch-generate-five.service 2>/dev/null || true
systemctl --user start councilwatch-generate-five.service
echo "Five-story generation started."
echo "Follow progress with:"
echo "  journalctl --user -fu councilwatch-generate-five.service"
