# SuperMarks Monorepo

SuperMarks is organized as a two-project monorepo for Vercel:

- `backend/`: FastAPI + SQLModel API deployed as a Python serverless project.
- `frontend/`: Vite + React SPA deployed as a static frontend project.

## Strategy Lock

This repository is locked to **Strategy B: direct backend API calls only**.

- Frontend must call backend using `VITE_API_BASE_URL=https://<backend>/api`.
- No frontend `/api` proxy functions.
- Do not add frontend `/api` rewrites.

See `docs/ARCHITECTURE.md` for guardrails.

## Required Environment Variables

### Frontend

- `VITE_API_BASE_URL=https://<backend-domain>/api`
- `VITE_BACKEND_API_KEY=<backend-api-key>` (optional if backend auth is disabled)

### Backend

- `BACKEND_API_KEY=<backend-api-key>`
- `CORS_ALLOW_ORIGINS=https://<frontend-domain>`

## Local development

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .[dev]
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Set frontend `.env` with backend values:

```bash
VITE_API_BASE_URL=http://localhost:8000/api
VITE_BACKEND_API_KEY=<your-local-key>
```

## Vercel deployment (two projects)

### 1) Backend project

- Create a Vercel project with **Root Directory** = `backend`
- Uses `backend/api/index.py` as Python function entrypoint
- `backend/vercel.json` rewrites all backend paths to the function

### 2) Frontend project

- Create a separate Vercel project with **Root Directory** = `frontend`
- Build command: `npm run build`
- Output directory: `dist`
- SPA fallback routing is handled in `frontend/vercel.json`

## Deployment policy note

Git-based automatic deployments are disabled for both Vercel projects to avoid Hobby plan deployment-cap limits.
When you are ready to ship, deploy manually from the Vercel UI using **Redeploy**.
