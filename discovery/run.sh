#!/usr/bin/env bash
set -euo pipefail
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
mkdir -p data logs
STAMP="$(date '+%Y-%m-%d_%H-%M-%S')"
"$HERE/.venv/bin/python" "$HERE/run.py" 2>&1 | tee "$HERE/logs/discovery-$STAMP.log"
