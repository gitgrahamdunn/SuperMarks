#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"

if [ -f "${BACKEND_DIR}/.env.local" ]; then
  set -a
  # shellcheck disable=SC1091
  source "${BACKEND_DIR}/.env.local"
  set +a
fi

DEFAULT_DATA_DIR="${BACKEND_DIR}/data"
DATA_DIR="${SUPERMARKS_DATA_DIR:-${DATA_DIR:-${DEFAULT_DATA_DIR}}}"
SQLITE_PATH="${SUPERMARKS_SQLITE_PATH:-${SQLITE_PATH:-${DATA_DIR}/supermarks.db}}"
BACKUP_DIR="${1:-${SUPERMARKS_BACKUP_DIR:-${HOME}/supermarks-backups}}"
TIMESTAMP="$(date +%Y%m%dT%H%M%S)"

mkdir -p "${BACKUP_DIR}"

DB_BACKUP_PATH="${BACKUP_DIR}/supermarks-db-${TIMESTAMP}.sqlite3"
FILES_BACKUP_PATH="${BACKUP_DIR}/supermarks-files-${TIMESTAMP}.tar.gz"

python3 - "${SQLITE_PATH}" "${DB_BACKUP_PATH}" <<'PY'
from pathlib import Path
import sqlite3
import sys

source = Path(sys.argv[1])
target = Path(sys.argv[2])
if not source.exists():
    raise SystemExit(f"SQLite file not found: {source}")

target.parent.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
PY

if [ -d "${DATA_DIR}" ]; then
  tar \
    --exclude="$(basename "${SQLITE_PATH}")" \
    --exclude="*.sqlite3-shm" \
    --exclude="*.sqlite3-wal" \
    -czf "${FILES_BACKUP_PATH}" \
    -C "${DATA_DIR}" .
fi

echo "SQLite backup: ${DB_BACKUP_PATH}"
echo "Files backup:  ${FILES_BACKUP_PATH}"
