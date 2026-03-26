#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "[supermarks] Reconnecting system services..."

sudo "${SCRIPT_DIR}/install-supermarks-service.sh" --system

if [[ -f "${SCRIPT_DIR}/verify-local-prod.sh" ]]; then
  sudo "${SCRIPT_DIR}/verify-local-prod.sh" "http://127.0.0.1:8000"
fi

echo "[supermarks] Reconnect complete."
