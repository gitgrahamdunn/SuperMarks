#!/usr/bin/env bash
set -euo pipefail
ROOT="$(dirname "$0")/.."
BACKEND_STATUS=0

cd "$ROOT/backend"
uv venv .venv >/dev/null 2>&1 || true
source .venv/bin/activate
uv pip install -e .[dev] >/dev/null
if ! python -m pytest -q; then
  BACKEND_STATUS=$?
fi

cd "$ROOT/frontend"
npm install >/dev/null
npm run build

if [ "$BACKEND_STATUS" -ne 0 ]; then
  echo
  echo "Backend tests are failing; see output above. Frontend build passed."
  exit "$BACKEND_STATUS"
fi
