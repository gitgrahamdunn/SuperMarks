#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:8000}"

echo "Checking ${BASE_URL}/health"
curl --fail --silent --show-error "${BASE_URL}/health" >/dev/null

echo "Checking ${BASE_URL}/health/deep"
curl --fail --silent --show-error "${BASE_URL}/health/deep" >/dev/null

echo "Checking frontend shell at ${BASE_URL}/"
curl --fail --silent --show-error "${BASE_URL}/" | rg -q "<!doctype html>|<html"

echo "Checking SPA route at ${BASE_URL}/exams"
curl --fail --silent --show-error "${BASE_URL}/exams" | rg -q "<!doctype html>|<html"

echo "Local production verification passed."
