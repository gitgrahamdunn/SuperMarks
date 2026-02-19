# SuperMarks

SuperMarks is a single deployable application on Vercel:

- **Frontend UI** (Vite + React + TypeScript) is built as static assets from `frontend/`.
- **Backend API** (FastAPI + SQLModel) is served by a Python serverless function from `api/index.py` under `/api`.

## Repository Structure

```text
.
├── api/
│   └── index.py              # Vercel Python entrypoint for backend
├── app/                      # Backend application package
├── frontend/                 # Frontend application (static build output)
├── tests/
├── pyproject.toml
├── requirements.txt          # Vercel backend install manifest
└── vercel.json               # Vercel single deploy config (frontend + backend)
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

## Single Vercel Deployment

Configure one Vercel project with the **Project Root** set to this repository root and **no Root Directory override**.

- Build command is handled by `vercel.json`: `cd frontend && npm ci && npm run build`
- Output directory is handled by `vercel.json`: `frontend/dist`
- Backend is available at `/api`
- API docs are available at `/api/docs`

Recommended backend environment variables in Vercel:

- `SUPERMARKS_VERCEL_ENVIRONMENT=true`
- `SUPERMARKS_CORS_ORIGINS=https://<your-domain>`
- (optional) `SUPERMARKS_CORS_ALLOW_ORIGIN_REGEX=https://.*\.vercel\.app`

> Note: In Vercel serverless runtime, persistent filesystem writes are not available. With `SUPERMARKS_VERCEL_ENVIRONMENT=true`, runtime files are redirected to `/tmp`.

## Common Deployment Error Fixes Included

- ✅ **Read-only filesystem** on Vercel backend handled by `/tmp` runtime paths.
- ✅ **React Router 404 on refresh** fixed with frontend rewrite config.
- ✅ **CORS wildcard + credentials mismatch** resolved via explicit origin config + regex.

## Tests

```bash
pytest
```
