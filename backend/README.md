# SuperMarks Backend

FastAPI backend service for SuperMarks, designed to deploy as a dedicated Vercel project.

## Structure

```text
backend/
├── api/index.py      # Vercel Python function entrypoint
├── app/              # Backend application package
├── data/             # Local runtime data directory (sqlite/uploads)
├── tests/
├── pyproject.toml
├── requirements.txt
└── vercel.json
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

## Vercel deployment

Dependency source: Vercel installs Python dependencies from `pyproject.toml` for this backend project root; keep `requirements.txt` aligned only if used for local/manual installs.

Create a Vercel project with Root Directory set to `backend`.

- Entrypoint: `api/index.py`
- Routing: `vercel.json` rewrites all paths to `/api/index.py`

Optional environment variables:

- `SUPERMARKS_VERCEL_ENVIRONMENT=true`
- `SUPERMARKS_CORS_ORIGINS=https://<frontend-domain>`
- `SUPERMARKS_CORS_ALLOW_ORIGIN_REGEX=https://.*\.vercel\.app`
- `APP_VERSION=<git-sha-or-build-id>` (optional; exposed by `GET /version`)
- `DATABASE_URL=<postgres-connection-url>` (recommended for hosted/scalable production)
- `SUPERMARKS_ALLOW_PRODUCTION_SQLITE=1` (supported only for self-hosting outside Vercel)


Storage notes:

- Local development defaults to `./data` (inside `backend/data`).
- On Vercel (`VERCEL`/`VERCEL_ENV` detected), runtime files are written to `/tmp/supermarks`.
- `/tmp` on Vercel is ephemeral and not persistent across deployments/invocations. Use external storage (S3, Vercel Blob, etc.) for durable file persistence.



Persistence note:

- Hosted production: Blob stores files and `DATABASE_URL` stores metadata.
- Self-hosted low-cost production: local files plus SQLite on disk are supported.

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

## Fly.io deployment

If you want the same long-running background-job feel as local development, prefer Fly.io for the backend.

Included files:

- `Dockerfile`
- `.dockerignore`
- `fly.toml`

Suggested split:

- frontend on Vercel
- backend on Fly.io
- metadata in Neon Postgres
- files in Vercel Blob

Typical Fly flow:

```bash
cd backend
export FLYCTL_INSTALL="$HOME/.fly"
export PATH="$FLYCTL_INSTALL/bin:$PATH"

flyctl auth login
flyctl launch --copy-config --no-deploy
flyctl secrets set \
  DATABASE_URL=... \
  BACKEND_API_KEY=... \
  BACKEND_SESSION_SECRET=... \
  CORS_ALLOW_ORIGINS=https://<frontend-domain> \
  SUPERMARKS_LLM_PROVIDER=doubleword \
  SUPERMARKS_LLM_BASE_URL=https://api.doubleword.ai/v1 \
  SUPERMARKS_LLM_API_KEY=... \
  SUPERMARKS_KEY_PARSE_NANO_MODEL=Qwen/Qwen3-VL-30B-A3B-Instruct-FP8 \
  SUPERMARKS_KEY_PARSE_MINI_MODEL=Qwen/Qwen3-VL-30B-A3B-Instruct-FP8 \
  SUPERMARKS_FRONT_PAGE_PROVIDER=gemini \
  GEMINI_API_KEY=... \
  SUPERMARKS_FRONT_PAGE_MODEL=gemini-2.5-flash \
  BLOB_READ_WRITE_TOKEN=... \
  BLOB_PUBLIC_ACCESS=private

flyctl deploy
```

Notes:

- `auto_stop_machines = "off"` keeps one machine running so background intake threads behave more like local.
- `SUPERMARKS_ENV=production` is set in `fly.toml` so the app requires `DATABASE_URL`.
- Update the Vercel frontend `VITE_API_BASE_URL` to the Fly backend URL after deploy.
