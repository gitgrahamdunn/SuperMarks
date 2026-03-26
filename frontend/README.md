# SuperMarks Frontend

React + Vite frontend for SuperMarks, intended to be hosted as a static SPA.

Cloudflare Pages is the canonical hosted frontend target.

## Strategy B lock

Frontend calls backend directly using `VITE_API_BASE_URL`.

- `VITE_API_BASE_URL` must end with `/api`.
- Use `/api` for local Vite development with proxy.
- Use `https://<backend>/api` for Cloudflare Pages deployments.
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
VITE_API_BASE_URL=/api
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

## Cloudflare Pages deployment

- Project root: `frontend`
- Build command: `npm run build`
- Build output directory: `dist`
- `frontend/wrangler.toml` tracks the Pages build output for CLI-driven deploys
- SPA fallback routing is configured by `frontend/public/_redirects`
- Set `VITE_API_BASE_URL` to the Cloudflare backend URL, ending in `/api`
- Use Cloudflare Pages environment variables for hosted builds; the value must still end with `/api`
- File access now comes through backend-issued signed URLs backed by the backend storage provider (Cloudflare R2 in the hosted direction).

## Hosted preview note

Hosted previews are not self-contained.

- This frontend makes direct browser API calls.
- Preview deployments only work if `VITE_API_BASE_URL` points at a reachable public backend.
- If no public backend is available, use the local or Funnel-hosted workflow instead of hosted previews.
