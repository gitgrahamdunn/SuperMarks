# SuperMarks Frontend

React + Vite frontend for SuperMarks, deployed as a dedicated Vercel project.

## Strategy B lock

Frontend calls backend directly using `VITE_API_BASE_URL`.

- `VITE_API_BASE_URL` must be an absolute URL ending with `/api`.
- Do not add frontend `/api` proxy functions.
- Do not add frontend `/api` rewrites.

## Local development

```bash
cd frontend
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

Or from repo root:

```bash
./scripts/dev-frontend.sh
```

Set env vars:

```bash
VITE_API_BASE_URL=http://localhost:8000/api
VITE_BACKEND_API_KEY=<your-backend-api-key>
VITE_APP_VERSION=<git-sha-or-release-tag>
```

## Seeded browser acceptance tests

A deterministic local acceptance slice now exists for both real marking lanes:

- question-level capture/teacher marking
- front-page totals capture/confirmation

From `frontend/`:

```bash
npm install
npx playwright install chromium
npm run test:acceptance
```

What the test run does:

- reseeds `../artifacts/acceptance/`
- starts the backend on `127.0.0.1:8010` against the seeded sqlite DB
- starts the frontend on `127.0.0.1:4173`
- drives one end-to-end browser flow for each workflow lane

The seed generator lives at `../scripts/seed_acceptance.py` and writes `../artifacts/acceptance/seed-metadata.json` for stable local IDs.

## Vercel deployment

- Root Directory: `frontend`
- Build Command: `npm run build`
- Output Directory: `dist`
- SPA fallback routing is configured by `frontend/vercel.json`
