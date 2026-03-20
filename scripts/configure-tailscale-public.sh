#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-5173}"
TARGET_URL="http://127.0.0.1:${PORT}"

tailscale funnel --bg "${TARGET_URL}"
tailscale funnel status
