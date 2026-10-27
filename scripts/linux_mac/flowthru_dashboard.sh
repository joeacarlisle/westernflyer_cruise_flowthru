#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
exec "$PY" -m panel serve flowthru_holoviews.py --port 5007 --show
