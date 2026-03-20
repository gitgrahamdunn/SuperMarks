#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="${HOME}/.config/systemd/user"
UNIT_NAME="supermarks.service"

mkdir -p "${UNIT_DIR}"
install -m 0644 "${ROOT_DIR}/ops/systemd/${UNIT_NAME}" "${UNIT_DIR}/${UNIT_NAME}"
systemctl --user daemon-reload
systemctl --user enable --now "${UNIT_NAME}"
systemctl --user --no-pager --full status "${UNIT_NAME}"
