#!/usr/bin/env bash
set -euo pipefail

D1_BRIDGE_URL=${1:-${SUPERMARKS_D1_BRIDGE_URL:-}}
BRIDGE_TOKEN=${SUPERMARKS_D1_BRIDGE_TOKEN:-${BACKEND_API_KEY:-}}

if [[ -z "$D1_BRIDGE_URL" ]]; then
  echo "usage: $0 <d1-bridge-url>"
  echo "or set SUPERMARKS_D1_BRIDGE_URL"
  exit 1
fi

if [[ -z "$BRIDGE_TOKEN" ]]; then
  echo "SUPERMARKS_D1_BRIDGE_TOKEN or BACKEND_API_KEY is required"
  exit 1
fi

tmp_body=$(mktemp)
trap 'rm -f "$tmp_body"' EXIT

status_code=$(curl -sS -o "$tmp_body" -w '%{http_code}' -X POST "${D1_BRIDGE_URL%/}/health" \
  -H 'content-type: application/json' \
  -H "x-supermarks-bridge-token: $BRIDGE_TOKEN" \
  -d '{}')

if [[ "$status_code" != "200" ]]; then
  echo "D1 bridge health check failed with HTTP $status_code"
  sed -n '1,20p' "$tmp_body"
  exit 1
fi

bridge_json=$(cat "$tmp_body")

python3 - <<'PY' "$bridge_json"
import json, sys
payload = json.loads(sys.argv[1])
assert payload.get("ok") is True, payload
print("d1 bridge health ok")
PY

echo "smoke ok: ${D1_BRIDGE_URL%/}"
