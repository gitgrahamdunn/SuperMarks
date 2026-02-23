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
npm run dev
```

Set env vars:

```bash
VITE_API_BASE_URL=http://localhost:8000/api
VITE_BACKEND_API_KEY=<your-backend-api-key>
```

## Vercel deployment

- Root Directory: `frontend`
- Build Command: `npm run build`
- Output Directory: `dist`
- SPA fallback routing is configured by `frontend/vercel.json`
