# SuperMarks

SuperMarks is split into two deployable applications:

- **Backend API** (FastAPI + SQLModel) in the repository root.
- **Frontend UI** (Vite + React + TypeScript) in `frontend/`.

This structure supports separate Vercel deployments for faster iteration.

## Repository Structure

```text
.
├── api/
│   └── index.py              # Vercel Python entrypoint for backend
├── app/                      # Backend application package
├── frontend/                 # Frontend application (deploy separately)
├── tests/
├── pyproject.toml
├── requirements.txt          # Vercel backend install manifest
└── vercel.json               # Vercel backend routing/runtime config
```

## Local Development

### Backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

Set `VITE_API_BASE_URL` in `frontend/.env` as needed (for local backend, keep `http://localhost:8000`).

## Vercel Deployment

### 1) Backend deploy (from repository root)

Vercel uses:
- `vercel.json` at repo root
- `api/index.py` as the serverless entrypoint
- `requirements.txt` for Python dependencies

Recommended backend environment variables in Vercel:

- `SUPERMARKS_VERCEL_ENVIRONMENT=true`
- `SUPERMARKS_CORS_ORIGINS=https://<your-frontend-domain>`
- (optional) `SUPERMARKS_CORS_ALLOW_ORIGIN_REGEX=https://.*\.vercel\.app`

> Note: In Vercel serverless runtime, persistent filesystem writes are not available. With `SUPERMARKS_VERCEL_ENVIRONMENT=true`, runtime files are redirected to `/tmp`.

### 2) Frontend deploy (from `frontend/`)

Vercel uses:
- `frontend/vercel.json` for SPA rewrites (fixes 404 refresh on client-side routes)

Recommended frontend environment variable in Vercel:

- `VITE_API_BASE_URL=https://<your-backend-domain>`

## Common Deployment Error Fixes Included

- ✅ **Read-only filesystem** on Vercel backend handled by `/tmp` runtime paths.
- ✅ **React Router 404 on refresh** fixed with frontend rewrite config.
- ✅ **CORS wildcard + credentials mismatch** resolved via explicit origin config + regex.

## Tests

```bash
pytest
```
