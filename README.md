# SuperMarks Monorepo

SuperMarks now targets a Cloudflare-hosted stack with a local-first development loop.

## Deployment Plan (Current)

- Phase 1 (active): run local backend + local frontend for iteration and verification.
- Phase 2: expose the local backend publicly from your machine when a hosted frontend needs it.
- Phase 3: host the frontend on Cloudflare Pages, run the backend in Cloudflare Containers, and use Cloudflare R2 for uploaded files.

Today, normal development still runs locally first. Hosted direction is Cloudflare end to end.

- `backend/`: FastAPI + SQLModel API with local-first development and Cloudflare Containers as the hosted backend target.
- `frontend/`: Vite + React SPA hosted statically on Cloudflare Pages.

## Strategy Lock

This repository is locked to **Strategy B: direct backend API calls only**.

- Frontend must call backend using `VITE_API_BASE_URL`:
  - `/api` for local Vite dev (proxied to `http://127.0.0.1:8000`)
  - `https://<backend>/api` for hosted backend
- No frontend `/api` proxy functions.
- Do not add frontend `/api` rewrites.

See `docs/ARCHITECTURE.md` for guardrails and `docs/EXPERIMENTATION.md` for the future A/B testing reference.

## Required Environment Variables

### Frontend

- `VITE_API_BASE_URL=/api` in local development.
- `VITE_API_BASE_URL=https://<backend-domain>/api` in Cloudflare Pages deployments.
- `VITE_BACKEND_API_KEY=<backend-api-key>` (optional if backend auth is disabled)
- `VITE_APP_VERSION=<git-sha-or-release-tag>` (optional, shown in UI diagnostics)

### Backend

- `BACKEND_API_KEY=<backend-api-key>`
- `CORS_ALLOW_ORIGINS=https://<cloudflare-pages-frontend-domain>`
- `APP_VERSION=<git-sha-or-build-id>` (optional, served by `GET /version`)
- `DATABASE_URL=<postgres-connection-url>` (required for hosted Cloudflare deployment)
- `SUPERMARKS_STORAGE_BACKEND=s3`
- `SUPERMARKS_S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com`
- `SUPERMARKS_S3_BUCKET=<r2-bucket-name>`
- `SUPERMARKS_S3_ACCESS_KEY_ID=<r2-access-key-id>`
- `SUPERMARKS_S3_SECRET_ACCESS_KEY=<r2-secret-access-key>`
- `SUPERMARKS_S3_REGION=auto`
- `SUPERMARKS_S3_PUBLIC_BASE_URL=https://<public-r2-domain>` (optional)
- `SUPERMARKS_ALLOW_PRODUCTION_SQLITE=1` (supported for self-hosted low-cost production on your own machine)

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
VITE_API_BASE_URL=/api
VITE_BACKEND_API_KEY=<your-local-key>
```

## Hosted on this machine

There are now two local hosting modes:

### 1) Hosted dev stack

Use this only for active iteration:

```bash
./scripts/host-supermarks.sh
```

Notes:

- Frontend stays on Vite, so frontend code changes hot-reload automatically.
- Backend stays on `uvicorn --reload`, so backend code changes restart automatically.
- `frontend/.env.local` can stay on `VITE_API_BASE_URL=/api` because Vite proxies `/api` to the backend.

### 2) Local production-safe stack

Use this for reboot-safe public hosting from your machine:

```bash
./scripts/prepare-local-prod.sh
./scripts/install-supermarks-service.sh
./scripts/verify-local-prod.sh
```

To make this system-level (restarts automatically on reboot), run this one command:

```bash
sudo /home/graham/repos/SuperMarks/scripts/install-supermarks-service.sh --system
```

After that, future reconnects are one short command:

```bash
supermarks-reconnect
```

The shortest command is:

```bash
smarks
```

Readable alias:

```bash
supermarks-reconnect
```

What this does:

- builds the frontend once to `frontend/dist`
- runs one backend service on port `8000`
- serves the built SPA directly from the backend
- re-applies Tailscale Funnel to the backend on boot
- avoids `vite dev`, `npm install`, and `uvicorn --reload` in the runtime boot path

### Current local verification status

- Frontend build passes locally
- Backend tests run locally under `uv`
- Current backend failures are concentrated in blob-mock behavior and answer-key parser dependency injection, not broad repo instability

## Hosted frontend deployment

This is Phase 3 and should only be used once the local/public backend path is stable.

### Static frontend host

- Host `frontend/` as a static SPA on Cloudflare Pages.
- Build command: `npm run build`
- Output directory: `dist`
- Keep `VITE_API_BASE_URL` pointed at the Cloudflare backend URL, ending in `/api`.
- Hosted previews are only usable when that backend URL is reachable and allowed by backend CORS.
- SPA fallback routing is provided by `frontend/public/_redirects`.
- `frontend/wrangler.toml` is the canonical hosted frontend config.

## Deployment policy note

Cloudflare Pages is the canonical hosted frontend, Cloudflare Containers is the canonical hosted backend, and Cloudflare R2 is the canonical durable object store.
When you are ready to ship:

1. deploy the backend from `backend/` with Wrangler
2. set backend secrets for `DATABASE_URL`, `BACKEND_API_KEY`, and R2 credentials
3. point frontend `VITE_API_BASE_URL` at the backend Worker URL or custom domain

## Recommended improvement loop

### Phase-1 local reconnect checklist

- Start backend: `./scripts/dev-backend.sh`
- Start frontend: `./scripts/dev-frontend.sh`
- Verify local stack: `./scripts/verify-local.sh`
- Confirm browser calls are `http://localhost:8000/api` (or `/api` with Vite proxy) in DevTools.

For day-to-day product polish, do not use production deploys as the feedback loop.

- Run the frontend locally with Vite for fast UI iteration.
- Run the backend locally for normal development, or point the local frontend at the hosted backend only when you specifically need to verify hosted behavior.
- Use the local/Tailscale-hosted flow for browser and mobile checks while iterating.
- Batch related UI fixes together, then do one production deploy after the slice is actually ready.

Practical rule:

- local build + browser smoke first
- production redeploy only for release-ready batches, not for each small tweak

## Hosted frontend versioning

For easier deploy verification, set `VITE_APP_VERSION` on the Cloudflare Pages deployment.

## Hosted backend deployment

Backend hosting now lives under [backend/wrangler.toml](/home/graham/repos/SuperMarks/backend/wrangler.toml) and [backend/cloudflare/index.js](/home/graham/repos/SuperMarks/backend/cloudflare/index.js).

One-time setup:

```bash
cd backend
npm install
wrangler login
```

Set secrets and deploy:

```bash
wrangler secret put DATABASE_URL
wrangler secret put BACKEND_API_KEY
wrangler secret put SUPERMARKS_S3_ACCESS_KEY_ID
wrangler secret put SUPERMARKS_S3_SECRET_ACCESS_KEY
wrangler secret put SUPERMARKS_S3_BUCKET
wrangler secret put SUPERMARKS_S3_ENDPOINT_URL
wrangler secret put SUPERMARKS_S3_PUBLIC_BASE_URL
wrangler secret put SUPERMARKS_CORS_ALLOW_ORIGINS
wrangler deploy
```

Non-secret defaults and local Wrangler examples live in [backend/.dev.vars.example](/home/graham/repos/SuperMarks/backend/.dev.vars.example).

## Persistence requirements

- Cloudflare R2 stores uploaded files/binaries in the hosted direction.
- `DATABASE_URL` stores all metadata (exams, questions, key files, submissions, pages, parse jobs).
- Hosted production should use R2 plus `DATABASE_URL`.
- Self-hosted low-cost production can use local files plus SQLite on disk instead.

## Low-cost self-hosted mode

For `0–10` users, the cheapest practical path is:

- frontend and/or backend hosted from your own machine
- backend on SQLite
- backend file storage on local disk
- public reachability through the existing Tailscale-hosted flow

Recommended env shape for that mode:

```bash
SUPERMARKS_ENV=production
SUPERMARKS_ALLOW_PRODUCTION_SQLITE=1
SUPERMARKS_STORAGE_BACKEND=local
SUPERMARKS_SERVE_FRONTEND=1
SUPERMARKS_DATA_DIR=/absolute/path/to/supermarks-data
SUPERMARKS_SQLITE_PATH=/absolute/path/to/supermarks-data/supermarks.db
```

Backup helper:

```bash
./scripts/backup-supermarks.sh
```

Reboot-safe verification:

```bash
./scripts/verify-local-prod.sh
```

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
