#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cd "${ROOT_DIR}/backend"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required to prepare the backend environment." >&2
  exit 1
fi

uv venv .venv >/dev/null 2>&1 || true
source .venv/bin/activate
uv pip install -e .[dev]

"${ROOT_DIR}/scripts/build-frontend-prod.sh"
