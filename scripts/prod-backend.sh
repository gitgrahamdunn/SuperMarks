#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
FRONTEND_DIST_DIR="${ROOT_DIR}/frontend/dist"
HOSTED_LOCAL_ENV_FILE="${BACKEND_DIR}/.env.hosted-local"
LOCAL_ENV_FILE="${BACKEND_DIR}/.env.local"

cd "${BACKEND_DIR}"

if [ -f "${HOSTED_LOCAL_ENV_FILE}" ]; then
  set -a
  source "${HOSTED_LOCAL_ENV_FILE}"
  set +a
elif [ -f "${LOCAL_ENV_FILE}" ]; then
  set -a
  source "${LOCAL_ENV_FILE}"
  set +a
fi

export SUPERMARKS_ENV="${SUPERMARKS_ENV:-production}"
export SUPERMARKS_ALLOW_PRODUCTION_SQLITE="${SUPERMARKS_ALLOW_PRODUCTION_SQLITE:-1}"
export SUPERMARKS_STORAGE_BACKEND="${SUPERMARKS_STORAGE_BACKEND:-local}"
export SUPERMARKS_REPOSITORY_BACKEND="${SUPERMARKS_REPOSITORY_BACKEND:-sqlmodel}"
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

if [ "${SUPERMARKS_STORAGE_BACKEND}" = "s3" ]; then
  : "${SUPERMARKS_S3_ENDPOINT_URL:?SUPERMARKS_S3_ENDPOINT_URL is required when SUPERMARKS_STORAGE_BACKEND=s3}"
  : "${SUPERMARKS_S3_BUCKET:?SUPERMARKS_S3_BUCKET is required when SUPERMARKS_STORAGE_BACKEND=s3}"
  : "${SUPERMARKS_S3_ACCESS_KEY_ID:?SUPERMARKS_S3_ACCESS_KEY_ID is required when SUPERMARKS_STORAGE_BACKEND=s3}"
  : "${SUPERMARKS_S3_SECRET_ACCESS_KEY:?SUPERMARKS_S3_SECRET_ACCESS_KEY is required when SUPERMARKS_STORAGE_BACKEND=s3}"
fi

if [ "${SUPERMARKS_REPOSITORY_BACKEND}" = "d1-bridge" ]; then
  : "${SUPERMARKS_D1_BRIDGE_URL:?SUPERMARKS_D1_BRIDGE_URL is required when SUPERMARKS_REPOSITORY_BACKEND=d1-bridge}"
  if [ -z "${SUPERMARKS_D1_BRIDGE_TOKEN:-}" ] && [ -z "${BACKEND_API_KEY:-}" ]; then
    echo "SUPERMARKS_D1_BRIDGE_TOKEN or BACKEND_API_KEY is required when SUPERMARKS_REPOSITORY_BACKEND=d1-bridge" >&2
    exit 1
  fi
fi

exec .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
