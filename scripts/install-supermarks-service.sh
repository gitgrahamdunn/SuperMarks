#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="${HOME}/.config/systemd/user"
BACKEND_UNIT="supermarks-backend.service"
PUBLIC_UNIT="supermarks-public.service"
LEGACY_UNIT="supermarks.service"

mkdir -p "${UNIT_DIR}"

"${ROOT_DIR}/scripts/prepare-local-prod.sh"

install -m 0644 "${ROOT_DIR}/ops/systemd/${BACKEND_UNIT}" "${UNIT_DIR}/${BACKEND_UNIT}"
install -m 0644 "${ROOT_DIR}/ops/systemd/${PUBLIC_UNIT}" "${UNIT_DIR}/${PUBLIC_UNIT}"
systemctl --user daemon-reload

systemctl --user disable --now "${LEGACY_UNIT}" 2>/dev/null || true

systemctl --user enable --now "${BACKEND_UNIT}" "${PUBLIC_UNIT}"
systemctl --user --no-pager --full status "${BACKEND_UNIT}"
systemctl --user --no-pager --full status "${PUBLIC_UNIT}"
