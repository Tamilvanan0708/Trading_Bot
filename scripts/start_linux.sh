#!/usr/bin/env bash
# Start the XAU/USD Trading Agent on Linux.
# Creates a virtualenv on first run, installs deps, and runs uvicorn with a
# single worker (required for SQLite + scheduler).  Restarts automatically.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d ".venv" ]; then
    echo "[setup] Creating virtualenv..."
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt

echo "[start] Launching XAU/USD agent (single worker)..."
while true; do
    uvicorn app.api.app:app --host 0.0.0.0 --port 8000 --workers 1
    echo "[restart] Process exited ($?). Restarting in 5s..."
    sleep 5
done