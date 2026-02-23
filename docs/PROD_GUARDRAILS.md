# Production Guardrails

## Before merging any PR that touches:
- `vercel.json`
- `api/*`
- routing
- CORS
- API base URL

You must:
- Confirm smoke tests pass
- Confirm no rewrites for `/api` exist
- Confirm only one API routing strategy is active
