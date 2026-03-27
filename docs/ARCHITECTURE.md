# Architecture

## Strategy B (Locked): Direct Backend API

SuperMarks is locked to **Strategy B**:

- The browser calls the backend directly via `VITE_API_BASE_URL`.
- `VITE_API_BASE_URL` must end with `/api`. In production this is an absolute URL; in local Vite dev it can be `/api` with proxy.
- There are **no frontend `/api` proxy functions** for application logic.
- Frontend deploy config is SPA-only; do not add `/api` rewrites.
- Hosted target is Cloudflare Pages -> Render backend URL -> FastAPI service.

## Rules and Guardrails

1. Do not add `frontend/api/*` proxy handlers.
2. Do not add root `api/*` proxy handlers for frontend routing.
3. Do not rely on same-origin `/api` for browser requests in production.
4. Keep backend CORS configured for frontend origins via `CORS_ALLOW_ORIGINS`.
5. Ensure OPTIONS preflight remains unauthenticated.

## Required Environment Variables

### Frontend

- `VITE_API_BASE_URL=/api` for local Vite dev.
- `VITE_API_BASE_URL=https://<backend-domain>/api` for Cloudflare Pages deployments.
- `VITE_BACKEND_API_KEY=<backend-api-key>` (optional if backend auth disabled)

### Backend

- `BACKEND_API_KEY=<backend-api-key>`
- `SUPERMARKS_CORS_ALLOW_ORIGINS=https://<cloudflare-pages-frontend-domain>`

## Smoke Checks

- Frontend API base validation blocks production boot when invalid.
- Frontend diagnostics card can ping backend health endpoint.
- Backend tests validate preflight behavior and API-key-protected exam creation.


## Persistence model

- Cloudflare R2 persists file content (exam keys, submission uploads, page images) in the hosted direction.
- Cloudflare D1 persists hosted metadata through the standalone Worker-side D1 bridge.
- Render hosts the FastAPI compute layer that talks to both systems.
