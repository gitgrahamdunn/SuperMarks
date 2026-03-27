# SuperMarks Backend

FastAPI backend service for SuperMarks, intended to run locally during development and on Render for hosted deployment.

## Structure

```text
backend/
├── app/              # Backend application package
├── data/             # Local runtime data directory (sqlite/uploads)
├── tests/
├── pyproject.toml
└── requirements.txt
```

## Local development

Preferred local path on this machine uses `uv`:

```bash
cd backend
uv venv .venv
source .venv/bin/activate
uv pip install -e .[dev]
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Or from repo root:

```bash
./scripts/dev-backend.sh
```

### OpenAI-compatible providers

The backend now supports OpenAI-compatible providers through env vars.

Example local config (`backend/.env.local`):

```bash
SUPERMARKS_LLM_PROVIDER=doubleword
SUPERMARKS_LLM_BASE_URL=https://api.doubleword.ai/v1
SUPERMARKS_LLM_API_KEY=your-key-here
SUPERMARKS_KEY_PARSE_NANO_MODEL=your-model-name
SUPERMARKS_KEY_PARSE_MINI_MODEL=your-model-name
```

If you want to force a single provider model for both parse stages, set both model vars to the same value.

Recommended shape for teacher-first key parsing:

- `SUPERMARKS_KEY_PARSE_NANO_MODEL` = faster visual first-pass model
- `SUPERMARKS_KEY_PARSE_MINI_MODEL` = stronger visual escalation model

The backend will keep clean pages on the fast path and only escalate suspicious pages based on structural heuristics.

Persistence note:

- Hosted production: Cloudflare R2 stores files and Cloudflare D1 stores metadata through a standalone Cloudflare Worker bridge.
- Self-hosted low-cost production: local files plus SQLite on disk are supported.

Recommended hosted storage shape:

```bash
SUPERMARKS_STORAGE_BACKEND=s3
SUPERMARKS_S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
SUPERMARKS_S3_BUCKET=<r2-bucket-name>
SUPERMARKS_S3_ACCESS_KEY_ID=<r2-access-key-id>
SUPERMARKS_S3_SECRET_ACCESS_KEY=<r2-secret-access-key>
SUPERMARKS_S3_REGION=auto
SUPERMARKS_S3_PUBLIC_BASE_URL=https://<public-r2-domain>   # optional
```

## Cloudflare D1 bridge worker

Cloudflare now hosts only the standalone D1 bridge Worker. The FastAPI backend runs on Render and calls that Worker over `SUPERMARKS_D1_BRIDGE_URL`.

Files:

- [wrangler.toml](/home/graham/repos/SuperMarks/backend/wrangler.toml)
- [cloudflare/index.js](/home/graham/repos/SuperMarks/backend/cloudflare/index.js)
- [.dev.vars.example](/home/graham/repos/SuperMarks/backend/.dev.vars.example)
- [deploy-cloudflare-d1-bridge.sh](/home/graham/repos/SuperMarks/backend/scripts/deploy-cloudflare-d1-bridge.sh)
- [smoke-cloudflare-d1-bridge.sh](/home/graham/repos/SuperMarks/backend/scripts/smoke-cloudflare-d1-bridge.sh)
- [.dev.vars.example](/home/graham/repos/SuperMarks/backend/.dev.vars.example)
- [smoke-cloudflare-backend.sh](/home/graham/repos/SuperMarks/backend/scripts/smoke-cloudflare-backend.sh)
- [render.yaml](/home/graham/repos/SuperMarks/render.yaml)
- [.env.render.example](/home/graham/repos/SuperMarks/backend/.env.render.example)

Deploy the Worker:

```bash
cd backend
npm install
wrangler login
wrangler secret put SUPERMARKS_D1_BRIDGE_TOKEN
./scripts/deploy-cloudflare-d1-bridge.sh
```

Recommended Worker env shape:

```bash
SUPERMARKS_ENV=production
SUPERMARKS_D1_BRIDGE_TOKEN=<internal-bridge-token-or-backend-api-key>
```

Hosted D1 binding:

- `wrangler.toml` now binds `SUPERMARKS_DB` to Cloudflare D1 database `supermarksdb`
- the Worker exposes `/_supermarks/d1/*`
- external FastAPI backends should point `SUPERMARKS_D1_BRIDGE_URL` at the deployed Worker URL plus `/_supermarks/d1`

Bridge smoke check:

```bash
cd backend
SUPERMARKS_D1_BRIDGE_TOKEN=<internal-bridge-token-or-backend-api-key> \
./scripts/smoke-cloudflare-d1-bridge.sh https://<your-d1-bridge-worker-domain>/_supermarks/d1
```

## Hosted FastAPI backend on Render

Render is now the canonical hosted backend target.

Create one `web` service from [render.yaml](/home/graham/repos/SuperMarks/render.yaml), then set the hosted backend env from [`.env.render.example`](/home/graham/repos/SuperMarks/backend/.env.render.example).

Current Render service shape:

```yaml
type: web
name: supermarks-backend
runtime: python
plan: starter
region: oregon
rootDir: backend
buildCommand: pip install -e .
startCommand: python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
healthCheckPath: /health
```

Hosted backend env:

```bash
SUPERMARKS_ENV=production
SUPERMARKS_MANAGED_RUNTIME_ENVIRONMENT=1
SUPERMARKS_STORAGE_BACKEND=s3
SUPERMARKS_REPOSITORY_BACKEND=d1-bridge
SUPERMARKS_S3_ENDPOINT_URL=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
SUPERMARKS_S3_BUCKET=<R2_BUCKET_NAME>
SUPERMARKS_S3_ACCESS_KEY_ID=<R2_ACCESS_KEY_ID>
SUPERMARKS_S3_SECRET_ACCESS_KEY=<R2_SECRET_ACCESS_KEY>
SUPERMARKS_S3_REGION=auto
SUPERMARKS_S3_PUBLIC_BASE_URL=https://<public-r2-domain>   # optional
BACKEND_API_KEY=<backend-api-key>
SUPERMARKS_D1_BRIDGE_TOKEN=<internal-bridge-token-or-backend-api-key>
SUPERMARKS_D1_BRIDGE_URL=https://<your-d1-bridge-worker-domain>/_supermarks/d1
SUPERMARKS_CORS_ALLOW_ORIGINS=https://<your-pages-domain>
SUPERMARKS_AUTH_SESSION_SECRET=<strong-random-secret>
SUPERMARKS_AUTH_ALLOWED_RETURN_ORIGINS=https://<your-pages-domain>,http://localhost:5173
SUPERMARKS_MAGIC_LINK_LOGIN_ENABLED=1
SUPERMARKS_EMAIL_PROVIDER=log
SUPERMARKS_EMAIL_API_KEY=<resend-server-key>           # only when using resend
SUPERMARKS_EMAIL_FROM_ADDRESS=<verified-sender>        # only when using resend
SUPERMARKS_DEV_LOGIN_ENABLED=1                         # optional hidden browser-testing login
SUPERMARKS_DEV_LOGIN_KEY=<developer-testing-passphrase>
SUPERMARKS_DEV_LOGIN_EMAIL=codex-dev@supermarks.local
SUPERMARKS_DEV_LOGIN_NAME=Codex Dev
SUPERMARKS_OIDC_PROVIDERS_JSON=
SUPERMARKS_SERVE_FRONTEND=0
SUPERMARKS_LLM_PROVIDER=doubleword
SUPERMARKS_LLM_BASE_URL=https://api.doubleword.ai/v1
SUPERMARKS_LLM_API_KEY=<doubleword-api-key>
SUPERMARKS_KEY_PARSE_NANO_MODEL=Qwen/Qwen3-VL-30B-A3B-Instruct-FP8
SUPERMARKS_KEY_PARSE_MINI_MODEL=Qwen/Qwen3-VL-30B-A3B-Instruct-FP8
SUPERMARKS_FRONT_PAGE_PROVIDER=gemini
GEMINI_API_KEY=<gemini-api-key>
SUPERMARKS_FRONT_PAGE_MODEL=gemini-2.5-flash
```

Render deploy flow:

```bash
render blueprints validate
```

Then sync the Blueprint in Render and deploy the `supermarks-backend` service.

Magic link notes:

- `SUPERMARKS_MAGIC_LINK_LOGIN_ENABLED=1` enables the email login UI and backend endpoints.
- `SUPERMARKS_AUTH_SESSION_SECRET` is required because magic-link verification issues app bearer tokens.
- `SUPERMARKS_EMAIL_PROVIDER=log` is acceptable for temporary hosted testing; the login link will be emitted to Render logs instead of being sent.
- For real delivery, switch to `SUPERMARKS_EMAIL_PROVIDER=resend` and set `SUPERMARKS_EMAIL_API_KEY` plus `SUPERMARKS_EMAIL_FROM_ADDRESS`.
- `SUPERMARKS_DEV_LOGIN_ENABLED=1` plus `SUPERMARKS_DEV_LOGIN_KEY` enables the hidden developer-only login used for browser automation.
- The hidden developer login reveals from the frontend login screen after tapping the heading five times.
- Google/Apple OIDC are optional and are not part of the canonical hosted configuration.

Hosted backend smoke check:

```bash
cd backend
SUPERMARKS_D1_BRIDGE_TOKEN=<internal-bridge-token-or-backend-api-key> \
SUPERMARKS_D1_BRIDGE_URL=https://<your-d1-bridge-worker-domain>/_supermarks/d1 \
./scripts/smoke-cloudflare-backend.sh https://<your-render-backend-domain>
```

Cloudflare Pages should then point:

```bash
VITE_API_BASE_URL=https://<your-render-backend-domain>/api
VITE_BACKEND_API_KEY=<backend-api-key>
```

## Local hosted mode: local backend + hosted frontend

This remains useful for local verification and troubleshooting, but it is no longer the canonical hosted path.

Setup shape:

1. copy [`.env.hosted-local.example`](/home/graham/repos/SuperMarks/backend/.env.hosted-local.example) to `backend/.env.hosted-local`
2. fill in the D1 bridge, R2, API key, and production Pages origin values
3. install/reinstall the reboot-safe service:

```bash
sudo /home/graham/repos/SuperMarks/scripts/install-supermarks-service.sh --system
```

4. verify local runtime:

```bash
/home/graham/repos/SuperMarks/scripts/verify-local-prod.sh http://127.0.0.1:8000
```

5. if you intentionally use this local mode, point Cloudflare Pages production `VITE_API_BASE_URL` at the machine's public Funnel URL ending in `/api`

Notes:

- this mode is `API-only`; the backend does not serve the SPA
- support only the production Pages frontend in `SUPERMARKS_CORS_ALLOW_ORIGINS`
- preview frontends remain intentionally unsupported in this mode
- Render should be the only hosted backend target for normal production use

## Self-hosted low-cost mode

If you want the cheapest practical setup for `0–10` users, run the backend on your own machine with:

```bash
SUPERMARKS_ENV=production
SUPERMARKS_ALLOW_PRODUCTION_SQLITE=1
SUPERMARKS_STORAGE_BACKEND=local
SUPERMARKS_SERVE_FRONTEND=1
SUPERMARKS_DATA_DIR=/absolute/path/to/supermarks-data
SUPERMARKS_SQLITE_PATH=/absolute/path/to/supermarks-data/supermarks.db
```

Recommended workflow:

- prepare the local production runtime once:

```bash
./scripts/prepare-local-prod.sh
```

- install the reboot-safe user services:

```bash
./scripts/install-supermarks-service.sh
```

- verify the stack after boot or after updates:

```bash
./scripts/verify-local-prod.sh
```

- run backups regularly with:

```bash
./scripts/backup-supermarks.sh
```

Local production notes:

- the backend serves the built SPA from `frontend/dist`
- runtime boot does not install dependencies
- runtime boot does not use `uvicorn --reload`
- Tailscale Funnel is re-applied against backend port `8000`
