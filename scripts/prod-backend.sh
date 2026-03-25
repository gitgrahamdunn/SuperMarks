#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
FRONTEND_DIST_DIR="${ROOT_DIR}/frontend/dist"

cd "${BACKEND_DIR}"

if [ -f .env.local ]; then
  set -a
  source .env.local
  set +a
fi

export SUPERMARKS_ENV="${SUPERMARKS_ENV:-production}"
export SUPERMARKS_ALLOW_PRODUCTION_SQLITE="${SUPERMARKS_ALLOW_PRODUCTION_SQLITE:-1}"
export SUPERMARKS_STORAGE_BACKEND="${SUPERMARKS_STORAGE_BACKEND:-local}"
export SUPERMARKS_SERVE_FRONTEND="${SUPERMARKS_SERVE_FRONTEND:-1}"
export SUPERMARKS_FRONTEND_DIST_DIR="${SUPERMARKS_FRONTEND_DIST_DIR:-${FRONTEND_DIST_DIR}}"
export PYTHONUNBUFFERED=1

if [ ! -x .venv/bin/uvicorn ]; then
  echo "Backend virtualenv is missing. Run ./scripts/prepare-local-prod.sh first." >&2
  exit 1
fi

if [ "${SUPERMARKS_SERVE_FRONTEND}" = "1" ] && [ ! -f "${SUPERMARKS_FRONTEND_DIST_DIR}/index.html" ]; then
  echo "Frontend build is missing at ${SUPERMARKS_FRONTEND_DIST_DIR}. Run ./scripts/build-frontend-prod.sh first." >&2
  exit 1
fi

exec .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
