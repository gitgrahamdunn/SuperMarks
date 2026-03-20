#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

frontend_pid=""
backend_pid=""

cleanup() {
  local code=$?
  trap - EXIT INT TERM

  if [[ -n "${frontend_pid}" ]] && kill -0 "${frontend_pid}" 2>/dev/null; then
    kill "${frontend_pid}" 2>/dev/null || true
  fi

  if [[ -n "${backend_pid}" ]] && kill -0 "${backend_pid}" 2>/dev/null; then
    kill "${backend_pid}" 2>/dev/null || true
  fi

  wait "${frontend_pid}" 2>/dev/null || true
  wait "${backend_pid}" 2>/dev/null || true
  exit "${code}"
}

trap cleanup EXIT INT TERM

"${ROOT_DIR}/scripts/dev-backend.sh" &
backend_pid=$!

"${ROOT_DIR}/scripts/dev-frontend.sh" &
frontend_pid=$!

wait -n "${backend_pid}" "${frontend_pid}"
