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

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .[dev]
uvicorn app.main:app --reload --port 8000
```

## Vercel deployment

Create a Vercel project with Root Directory set to `backend`.

- Entrypoint: `api/index.py`
- Routing: `vercel.json` rewrites all paths to `/api/index.py`

Optional environment variables:

- `SUPERMARKS_VERCEL_ENVIRONMENT=true`
- `SUPERMARKS_CORS_ORIGINS=https://<frontend-domain>`
- `SUPERMARKS_CORS_ALLOW_ORIGIN_REGEX=https://.*\.vercel\.app`


Storage notes:

- Local development defaults to `./data` (inside `backend/data`).
- On Vercel (`VERCEL`/`VERCEL_ENV` detected), runtime files are written to `/tmp/supermarks`.
- `/tmp` on Vercel is ephemeral and not persistent across deployments/invocations. Use external storage (S3, Vercel Blob, etc.) for durable file persistence.

