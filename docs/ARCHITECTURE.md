# Architecture

## API Routing Strategy (Locked)

### We use:
- Frontend serverless proxy (Strategy A)
- No `/api` rewrites in `vercel.json`
- SPA fallback only for non-api routes

### We do NOT:
- Mix rewrites and proxy functions
- Call backend directly from browser (unless explicitly documented)
- Create specific `api/*.js` files that shadow catch-all unintentionally

### Known failure modes:
- If `GET /api/exams` returns `text/html` → routing broken
- If `POST /api/exams` returns `405` empty → route shadowing
- If no backend logs → request never left frontend

### Smoke tests:
- `GET /api/proxy-health` must return JSON
- `GET /api/exams` must return JSON (`401` acceptable in browser)
- `POST /api/exams` must reach backend logs
