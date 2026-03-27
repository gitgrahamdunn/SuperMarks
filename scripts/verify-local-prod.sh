#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:8000}"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
SERVE_FRONTEND="${SUPERMARKS_SERVE_FRONTEND:-}"

has_rg() {
  command -v rg >/dev/null 2>&1
}

html_matches() {
  if has_rg; then
    rg -q "<!doctype html>|<html"
  else
    grep -Eqi "<!doctype html>|<html"
  fi
}

api_root_matches() {
  if has_rg; then
    rg -q '"service"[[:space:]]*:[[:space:]]*"supermarks-backend"'
  else
    grep -Eq '"service"[[:space:]]*:[[:space:]]*"supermarks-backend"'
  fi
}

if [[ -z "${SERVE_FRONTEND}" && -f "${BACKEND_DIR}/.env.hosted-local" ]]; then
  set -a
  source "${BACKEND_DIR}/.env.hosted-local"
  set +a
  SERVE_FRONTEND="${SUPERMARKS_SERVE_FRONTEND:-}"
elif [[ -z "${SERVE_FRONTEND}" && -f "${BACKEND_DIR}/.env.local" ]]; then
  set -a
  source "${BACKEND_DIR}/.env.local"
  set +a
  SERVE_FRONTEND="${SUPERMARKS_SERVE_FRONTEND:-}"
fi

SERVE_FRONTEND="${SERVE_FRONTEND:-1}"

echo "Checking ${BASE_URL}/health"
curl --fail --silent --show-error "${BASE_URL}/health" >/dev/null

echo "Checking ${BASE_URL}/health/deep"
curl --fail --silent --show-error "${BASE_URL}/health/deep" >/dev/null

if [[ "${SERVE_FRONTEND}" == "1" ]]; then
  echo "Checking frontend shell at ${BASE_URL}/"
  curl --fail --silent --show-error "${BASE_URL}/" | html_matches

  echo "Checking SPA route at ${BASE_URL}/exams"
  curl --fail --silent --show-error "${BASE_URL}/exams" | html_matches
else
  echo "Checking API root at ${BASE_URL}/"
  curl --fail --silent --show-error "${BASE_URL}/" | api_root_matches
fi

echo "Local production verification passed."
