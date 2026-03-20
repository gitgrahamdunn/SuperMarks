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
- `VITE_APP_VERSION=<git-sha-or-release-tag>` (optional, shown in UI diagnostics)

### Backend

- `BACKEND_API_KEY=<backend-api-key>`
- `CORS_ALLOW_ORIGINS=https://<frontend-domain>`
- `APP_VERSION=<git-sha-or-build-id>` (optional, served by `GET /version`)
- `DATABASE_URL=<postgres-connection-url>` (**required in production** for metadata persistence)

## Local development

### Canonical local dev loop

Backend (uv-based, preferred on this machine):

```bash
./scripts/dev-backend.sh
```

Frontend:

```bash
./scripts/dev-frontend.sh
```

Verification:

```bash
./scripts/verify-local.sh
```

### Ports

- Backend: `http://localhost:8000`
- Frontend: `http://localhost:5173`

Set frontend `.env` with backend values:

```bash
VITE_API_BASE_URL=http://localhost:8000/api
VITE_BACKEND_API_KEY=<your-local-key>
```

## Hosted on this machine

This repo now includes a simple hosted-dev setup that keeps both services running locally and exposes the frontend over Tailscale HTTPS.

Start the combined app manually:

```bash
./scripts/host-supermarks.sh
```

Install it as a user service:

```bash
./scripts/install-supermarks-service.sh
```

Expose it publicly through Tailscale Funnel:

```bash
./scripts/configure-tailscale-public.sh
```

Notes:

- Frontend stays on Vite, so frontend code changes hot-reload automatically.
- Backend stays on `uvicorn --reload`, so backend code changes restart automatically.
- `frontend/.env.local` can stay on `VITE_API_BASE_URL=/api` because Vite proxies `/api` to the backend.
- If you need a different public hostname allowlist, set `VITE_ALLOWED_HOSTS=host1,host2` before starting the frontend service.

### Current local verification status

- Frontend build passes locally
- Backend tests run locally under `uv`
- Current backend failures are concentrated in blob-mock behavior and answer-key parser dependency injection, not broad repo instability

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

## Versioning on Vercel

For easier deploy verification, set both of these per deployment:

- Frontend: `VITE_APP_VERSION`
- Backend: `APP_VERSION`

A good value is the same Git SHA (or release tag) in both projects.

## Persistence requirements

- Blob storage stores uploaded files/binaries.
- `DATABASE_URL` stores all metadata (exams, questions, key files, submissions, pages, parse jobs).
- In production, both Blob storage and `DATABASE_URL` must be configured for full persistence.

## Current teacher-first workflow slice

The active product wedge now runs:

1. parse answer key
2. review and confirm parsed data, including direct flagged-page drilldown and real single-page parse retry from the exam workspace without reprocessing already-good pages
3. prepare a submission for marking, with stale-vs-missing asset detection after template changes
4. mark inside the teacher workspace, with auto-recovery blocked when teacher manual marking has already started on questions that would need rebuilt assets
5. monitor exam-level marking progress from the exam dashboard
6. export marks as CSV

### Exam dashboard signals

Each submission now exposes a dashboard workflow status:

- `blocked`: preparation is missing or cannot proceed automatically
- `ready`: prepared and ready for teacher marking
- `in_progress`: teacher has started manual marking but not finished all questions
- `complete`: every question has a teacher-entered mark

The dashboard also shows flagged-question count, running total, per-objective totals, and a teacher-facing class results/reporting table that makes export readiness clear per student. Objective rows now surface incomplete blockers and the weakest complete result together, with explicit open-first/then-review wording so teachers can see what to open first when both kinds of follow-up exist.

### Export surfaces

Current teacher-facing exports include:

- `GET /api/exams/{exam_id}/export.csv` — full-detail class CSV with totals, objectives, and per-question marks
- `GET /api/exams/{exam_id}/export-summary.csv` — one row per student with export posture, next return point, and reporting attention
- `GET /api/exams/{exam_id}/export-objectives-summary.csv` — one row per objective with class coverage and strongest/weakest complete results
- `GET /api/exams/{exam_id}/export-student-summaries.zip` — zip package with a top-level `README.txt`, class `manifest.csv`, one teacher-readable `summary.txt`, a printable `summary.html`, and question-level evidence files (`evidence/README.txt`, `evidence/manifest.csv`, answer crops, transcription text/json when available) per student
