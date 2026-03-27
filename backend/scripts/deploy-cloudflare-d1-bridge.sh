#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
BACKEND_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
DB_NAME=${SUPERMARKS_D1_DATABASE_NAME:-supermarksdb}

cd "$BACKEND_DIR"

wrangler d1 migrations apply "$DB_NAME" --remote
wrangler deploy
