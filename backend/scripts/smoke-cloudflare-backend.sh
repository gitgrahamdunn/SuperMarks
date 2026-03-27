#!/usr/bin/env bash
set -euo pipefail

BACKEND_URL=${1:-${SUPERMARKS_BACKEND_URL:-}}
BRIDGE_TOKEN=${SUPERMARKS_D1_BRIDGE_TOKEN:-${BACKEND_API_KEY:-}}
D1_BRIDGE_URL=${SUPERMARKS_D1_BRIDGE_URL:-${2:-}}

if [[ -z "$BACKEND_URL" ]]; then
  echo "usage: $0 <backend-url>"
  echo "or set SUPERMARKS_BACKEND_URL"
  exit 1
fi

BACKEND_URL=${BACKEND_URL%/}

post_json_checked() {
  local url=$1
  local tmp_body
  tmp_body=$(mktemp)
  local status_code
  status_code=$(curl -sS -o "$tmp_body" -w '%{http_code}' -X POST "$url" \
    -H 'content-type: application/json' \
    -H "x-supermarks-bridge-token: $BRIDGE_TOKEN" \
    -d '{}')
  if [[ "$status_code" != "200" ]]; then
    echo "POST $url failed with HTTP $status_code" >&2
    sed -n '1,20p' "$tmp_body" >&2
    rm -f "$tmp_body"
    return 1
  fi
  cat "$tmp_body"
  rm -f "$tmp_body"
}

health_json=$(curl -fsS "$BACKEND_URL/health")
python3 - <<'PY' "$health_json"
import json, sys
payload = json.loads(sys.argv[1])
assert payload.get("ok") is True, payload
print("health ok")
PY

deep_health_json=$(curl -fsS "$BACKEND_URL/health/deep")
python3 - <<'PY' "$deep_health_json"
import json, sys
payload = json.loads(sys.argv[1])
assert payload.get("ok") is True, payload
print("deep health ok")
PY

if [[ -n "$D1_BRIDGE_URL" && -n "$BRIDGE_TOKEN" ]]; then
  if ! bridge_json=$(post_json_checked "${D1_BRIDGE_URL%/}/health"); then
    exit 1
  fi
  python3 - <<'PY' "$bridge_json"
import json, sys
payload = json.loads(sys.argv[1])
assert payload.get("ok") is True, payload
print("standalone D1 bridge health ok")
PY
elif [[ -n "$D1_BRIDGE_URL" ]]; then
  echo "D1 bridge URL set but token missing; skipping standalone D1 bridge smoke"
elif [[ -n "$BRIDGE_TOKEN" ]]; then
  echo "bridge token set but D1 bridge URL missing; skipping standalone D1 bridge smoke"
else
  echo "D1 bridge smoke skipped; D1 bridge URL/token not set"
fi

echo "smoke ok: $BACKEND_URL"
