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
- `DATABASE_URL=<postgres-connection-url>` (**required in production**; stores all metadata)


Storage notes:

- Local development defaults to `./data` (inside `backend/data`).
- On Vercel (`VERCEL`/`VERCEL_ENV` detected), runtime files are written to `/tmp/supermarks`.
- `/tmp` on Vercel is ephemeral and not persistent across deployments/invocations. Use external storage (S3, Vercel Blob, etc.) for durable file persistence.



Persistence note: Blob stores files; DATABASE_URL stores metadata. Both are required for persistence in production.
