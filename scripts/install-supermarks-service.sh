#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_UNIT="supermarks-backend.service"
PUBLIC_UNIT="supermarks-public.service"
LEGACY_UNIT="supermarks.service"
SERVICE_MODE="user"

usage() {
  cat <<'USAGE'
Usage: ./scripts/install-supermarks-service.sh [--user|--system]

  --user    Install user-level services.
  --system  Install system-level services (recommended for reboot autostart).
  
Running with sudo without --user defaults to system-level.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "${1:-}" in
    --user)
      SERVICE_MODE="user"
      shift
      ;;
    --system)
      SERVICE_MODE="system"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      exit 1
      ;;
  esac
done

if [[ "${SERVICE_MODE}" == "user" && -n "${SUDO_USER:-}" ]]; then
  SERVICE_MODE="system"
fi

if [[ "${SERVICE_MODE}" == "system" ]]; then
  UNIT_DIR="/etc/systemd/system"
  SERVICE_USER="${SUPERMARKS_SERVICE_USER:-${SUDO_USER:-$(id -un)}}"
  SERVICE_GROUP="${SUPERMARKS_SERVICE_GROUP:-$(id -gn "${SERVICE_USER}")}"
  CONTROL=(sudo systemctl)
  sudo -u "${SERVICE_USER}" bash "${ROOT_DIR}/scripts/prepare-local-prod.sh"
else
  UNIT_DIR="${HOME}/.config/systemd/user"
  CONTROL=(systemctl --user)
  bash "${ROOT_DIR}/scripts/prepare-local-prod.sh"
fi

if [[ "${SERVICE_MODE}" == "system" ]]; then
  mkdir -p /tmp/supermarks-systemd
  cat > /tmp/supermarks-systemd/supermarks-reconnect.sh <<EOF
#!/usr/bin/env bash
set -euo pipefail

"${ROOT_DIR}/scripts/reconnect-supermarks.sh" "\$@"
EOF

  sudo install -m 0755 /tmp/supermarks-systemd/supermarks-reconnect.sh /usr/local/bin/supermarks-reconnect
  echo "Installed short command: supermarks-reconnect"
  sudo install -m 0755 /tmp/supermarks-systemd/supermarks-reconnect.sh /usr/local/bin/smarks
  echo "Installed short command: smarks"
  sudo install -m 0755 /tmp/supermarks-systemd/supermarks-reconnect.sh /usr/local/bin/supermarks
  echo "Installed short command: supermarks"

  cat > /tmp/supermarks-systemd/supermarks-backend.service <<EOF
[Unit]
Description=SuperMarks local production backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${ROOT_DIR}
ExecStart=${ROOT_DIR}/scripts/prod-backend.sh
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  cat > /tmp/supermarks-systemd/supermarks-public.service <<EOF
[Unit]
Description=SuperMarks public Tailscale Funnel
After=network-online.target supermarks-backend.service
Wants=network-online.target supermarks-backend.service

[Service]
Type=oneshot
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${ROOT_DIR}
ExecStart=${ROOT_DIR}/scripts/configure-tailscale-public.sh 8000
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

  sudo install -m 0644 /tmp/supermarks-systemd/supermarks-backend.service "${UNIT_DIR}/${BACKEND_UNIT}"
  sudo install -m 0644 /tmp/supermarks-systemd/supermarks-public.service "${UNIT_DIR}/${PUBLIC_UNIT}"
  rm -rf /tmp/supermarks-systemd
else
  mkdir -p "${UNIT_DIR}"
  install -m 0644 "${ROOT_DIR}/ops/systemd/${BACKEND_UNIT}" "${UNIT_DIR}/${BACKEND_UNIT}"
  install -m 0644 "${ROOT_DIR}/ops/systemd/${PUBLIC_UNIT}" "${UNIT_DIR}/${PUBLIC_UNIT}"
fi

"${CONTROL[@]}" daemon-reload

if [[ "${SERVICE_MODE}" == "system" ]]; then
  "${CONTROL[@]}" disable --now "${LEGACY_UNIT}" 2>/dev/null || true
fi

"${CONTROL[@]}" enable --now "${BACKEND_UNIT}" "${PUBLIC_UNIT}"

"${CONTROL[@]}" --no-pager --full status "${BACKEND_UNIT}"
"${CONTROL[@]}" --no-pager --full status "${PUBLIC_UNIT}"
