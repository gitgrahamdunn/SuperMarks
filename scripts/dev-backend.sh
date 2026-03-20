#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../backend"
if [ -f .env.local ]; then
  set -a
  source .env.local
  set +a
fi
uv venv .venv >/dev/null 2>&1 || true
source .venv/bin/activate
uv pip install -e .[dev]
exec uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
