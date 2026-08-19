#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
mkdir -p data logs
echo
echo "CouncilWatch Pi v2 installed."
echo "Run: $HERE/run.sh"
