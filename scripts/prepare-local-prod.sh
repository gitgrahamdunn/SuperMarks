#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UV_BOOTSTRAP_DIR="${ROOT_DIR}/.supermarks-uv"

export PATH="${HOME}/.local/bin:${PATH}"

resolve_uv() {
  if command -v uv >/dev/null 2>&1; then
    echo "uv"
    return 0
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required to prepare the backend environment." >&2
    echo "Please install python3 and retry." >&2
    exit 1
  fi

  if ! python3 -m venv --help >/dev/null 2>&1; then
    echo "python3 -m venv is required to bootstrap uv." >&2
    echo "Install python3-venv and retry." >&2
    exit 1
  fi

  if [[ -x "${UV_BOOTSTRAP_DIR}/bin/uv" ]]; then
    echo "${UV_BOOTSTRAP_DIR}/bin/uv"
    return 0
  fi

  echo "uv is required and missing. Bootstrapping a local uv environment..."
  mkdir -p "${UV_BOOTSTRAP_DIR}"
  rm -f "${UV_BOOTSTRAP_DIR}/uv.log"

  if ! python3 -m venv "${UV_BOOTSTRAP_DIR}" >>"${UV_BOOTSTRAP_DIR}/uv.log" 2>&1; then
    echo "uv bootstrap failed. python3 venv support is missing on this machine." >&2
    echo "Install the OS venv package and rerun." >&2
    echo "See ${UV_BOOTSTRAP_DIR}/uv.log for details." >&2
    exit 1
  fi

  UV_PY="${UV_BOOTSTRAP_DIR}/bin/python"

  "${UV_PY}" -m ensurepip --upgrade >>"${UV_BOOTSTRAP_DIR}/uv.log" 2>&1 || true

  if ! "${UV_PY}" -m pip --version >>"${UV_BOOTSTRAP_DIR}/uv.log" 2>&1; then
    echo "uv bootstrap failed: venv does not provide pip." >&2
    echo "Install the OS venv package and rerun." >&2
    echo "See ${UV_BOOTSTRAP_DIR}/uv.log for details." >&2
    exit 1
  fi

  "${UV_PY}" -m pip install -U pip uv >>"${UV_BOOTSTRAP_DIR}/uv.log" 2>&1

  if [[ ! -x "${UV_BOOTSTRAP_DIR}/bin/uv" ]]; then
    echo "uv bootstrap failed. See ${UV_BOOTSTRAP_DIR}/uv.log" >&2
    echo "Install uv manually and rerun." >&2
    exit 1
  fi

  echo "${UV_BOOTSTRAP_DIR}/bin/uv"
}

UV_CMD="$(resolve_uv)"

cd "${ROOT_DIR}/backend"

"${UV_CMD}" venv .venv >/dev/null 2>&1 || true
source .venv/bin/activate
"${UV_CMD}" pip install -e .[dev]

"${ROOT_DIR}/scripts/build-frontend-prod.sh"
