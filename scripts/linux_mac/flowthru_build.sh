#!/usr/bin/env bash
# Build the flow-through DuckDB for one cruise:  flowthru_build.sh <cruise_id> [options]
set -euo pipefail
cd "$(dirname "$0")/../.."
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
exec "$PY" main.py "$@"
