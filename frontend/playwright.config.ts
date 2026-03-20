import { defineConfig } from '@playwright/test';

const backendPort = 8010;
const frontendPort = 4173;

export default defineConfig({
  testDir: './tests/acceptance',
  fullyParallel: false,
  retries: 0,
  use: {
    baseURL: `http://127.0.0.1:${frontendPort}`,
    trace: 'on-first-retry',
  },
  webServer: [
    {
      command: `bash -lc 'set -euo pipefail; cd ../backend; PYTHONPATH=. ./.venv/bin/python ../scripts/seed_acceptance.py >/tmp/supermarks-acceptance-seed.log; OPENAI_MOCK=1 BACKEND_API_KEY=test-key SUPERMARKS_DATA_DIR=$(cd ../artifacts/acceptance/data && pwd) SUPERMARKS_SQLITE_PATH=$(cd ../artifacts/acceptance && pwd)/supermarks-acceptance.db PYTHONPATH=. ./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port ${backendPort}'`,
      url: `http://127.0.0.1:${backendPort}/health`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `bash -lc 'set -euo pipefail; VITE_API_BASE_URL=http://127.0.0.1:${backendPort}/api VITE_BACKEND_API_KEY=test-key VITE_APP_VERSION=acceptance-seed npm run dev -- --host 127.0.0.1 --port ${frontendPort}'`,
      url: `http://127.0.0.1:${frontendPort}`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
