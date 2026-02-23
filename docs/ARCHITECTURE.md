# Architecture

## Strategy B (Locked): Direct Backend API

SuperMarks is locked to **Strategy B**:

- The browser calls the backend directly via `VITE_API_BASE_URL`.
- `VITE_API_BASE_URL` must be an absolute URL and must end with `/api`.
- There are **no frontend `/api` proxy functions** for application logic.
- Frontend deploy config is SPA-only; do not add `/api` rewrites.

## Rules and Guardrails

1. Do not add `frontend/api/*` proxy handlers.
2. Do not add root `api/*` proxy handlers for frontend routing.
3. Do not rely on same-origin `/api` for browser requests.
4. Keep backend CORS configured for frontend origins via `CORS_ALLOW_ORIGINS`.
5. Ensure OPTIONS preflight remains unauthenticated.

## Required Environment Variables

### Frontend

- `VITE_API_BASE_URL=https://<backend-domain>/api`
- `VITE_BACKEND_API_KEY=<backend-api-key>` (optional if backend auth disabled)

### Backend

- `BACKEND_API_KEY=<backend-api-key>`
- `CORS_ALLOW_ORIGINS=https://<frontend-domain>`

## Smoke Checks

- Frontend API base validation blocks production boot when invalid.
- Frontend diagnostics card can ping backend health endpoint.
- Backend tests validate preflight behavior and API-key-protected exam creation.
