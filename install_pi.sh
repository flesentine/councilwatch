#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

echo "=== Installing ffmpeg ==="
sudo apt-get update
sudo apt-get install -y ffmpeg

echo "=== Creating Python environment ==="
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
mkdir -p drafts work

echo
echo "Installed CouncilWatch five-story private review."
