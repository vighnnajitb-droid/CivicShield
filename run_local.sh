#!/usr/bin/env bash
# Local (no Docker) start. Terminal 1: n8n.  Terminal 2: this script.
set -e; cd "$(dirname "$0")/backend"
: "${INTERNAL_API_KEY:?export INTERNAL_API_KEY first (same value as in n8n)}"
python -m venv ../.venv 2>/dev/null || true; . ../.venv/bin/activate; pip install -q -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
