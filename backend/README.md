# SuperMarks Backend

FastAPI backend service for SuperMarks, intended to run locally during development and in Cloudflare Containers for hosted deployment.

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

- Hosted production: Cloudflare R2 stores files and `DATABASE_URL` stores metadata.
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

## Cloudflare hosted backend

The hosted backend target is Cloudflare Containers, fronted by a Worker.

Files:

- [wrangler.toml](/home/graham/repos/SuperMarks/backend/wrangler.toml)
- [cloudflare/index.js](/home/graham/repos/SuperMarks/backend/cloudflare/index.js)
- [Dockerfile](/home/graham/repos/SuperMarks/backend/Dockerfile)
- [.dev.vars.example](/home/graham/repos/SuperMarks/backend/.dev.vars.example)

Deploy flow:

```bash
cd backend
npm install
wrangler login
wrangler secret put DATABASE_URL
wrangler secret put BACKEND_API_KEY
wrangler secret put SUPERMARKS_S3_ACCESS_KEY_ID
wrangler secret put SUPERMARKS_S3_SECRET_ACCESS_KEY
wrangler deploy
```

Recommended hosted env shape:

```bash
SUPERMARKS_ENV=production
SUPERMARKS_MANAGED_RUNTIME_ENVIRONMENT=1
SUPERMARKS_STORAGE_BACKEND=s3
SUPERMARKS_S3_ENDPOINT_URL=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
SUPERMARKS_S3_BUCKET=<R2_BUCKET_NAME>
SUPERMARKS_S3_ACCESS_KEY_ID=<R2_ACCESS_KEY_ID>
SUPERMARKS_S3_SECRET_ACCESS_KEY=<R2_SECRET_ACCESS_KEY>
SUPERMARKS_S3_REGION=auto
SUPERMARKS_S3_PUBLIC_BASE_URL=https://<public-r2-domain>   # optional
DATABASE_URL=postgresql://<user>:<password>@<host>:<port>/<database>
BACKEND_API_KEY=<backend-api-key>
SUPERMARKS_CORS_ALLOW_ORIGINS=https://<your-pages-domain>
SUPERMARKS_SERVE_FRONTEND=0
```

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
