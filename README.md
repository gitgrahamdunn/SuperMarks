# SuperMarks Monorepo

SuperMarks is organized as a two-project monorepo for Vercel:

- `backend/`: FastAPI + SQLModel API deployed as a Python serverless project.
- `frontend/`: Vite + React SPA deployed as a static frontend project.

## Repository layout

```text
.
├── backend/
│   ├── app/
│   ├── api/index.py
│   ├── pyproject.toml
│   ├── vercel.json
│   └── README.md
├── frontend/
│   ├── src/
│   ├── .env.production
│   ├── vercel.json
│   └── README.md
├── .gitignore
└── README.md
```

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

The frontend API client defaults to same-origin `/api` (so production traffic stays on the frontend domain). For local development, set `VITE_API_BASE_URL` explicitly (for example `http://localhost:8000/api`).

## Vercel deployment (two projects)

### 1) Backend project

- Create a Vercel project with **Root Directory** = `backend`
- Uses `backend/api/index.py` as Python function entrypoint
- `backend/vercel.json` rewrites all backend paths to the function

### 2) Frontend project

- Create a separate Vercel project with **Root Directory** = `frontend`
- Build command: `npm run build`
- Output directory: `dist`
- SPA routing fallback is handled in `frontend/vercel.json`

Do not set `VITE_API_BASE_URL` in frontend production env vars (or set it to an empty value) so production uses `/api` and the Vercel rewrite proxy.

## Deployment policy note

Git-based automatic deployments are disabled for both Vercel projects to avoid Hobby plan deployment-cap limits.
When you are ready to ship, deploy manually from the Vercel UI using **Redeploy**.

## API proxy routing

Repo-root Vercel functions in `api/*.js` own all `/api/*` routes for the frontend domain.
This keeps `/api` traffic out of SPA fallback routing and ensures frontend requests always reach serverless proxy functions.

- `GET /api/proxy-health` -> health response from repo-root function
- `GET /api/exams-create` -> POST passthrough helper to backend `/api/exams`
- `GET/POST/... /api/*` -> catch-all proxy to backend (`/api/openapi.json` maps to backend `/openapi.json`)
