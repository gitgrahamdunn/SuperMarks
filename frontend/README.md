# SuperMarks Frontend

React + Vite frontend for SuperMarks, deployed as a dedicated Vercel project.

## Local development

```bash
cd frontend
npm install
npm run dev
```

Set `VITE_API_BASE_URL` to your backend API base URL (must include `/api`), for example:

- `http://localhost:8000/api`
- `https://your-backend.example.com/api`

Optionally set `VITE_BACKEND_API_KEY` if backend API key auth is enabled.

## Production configuration

`VITE_API_BASE_URL` is required in production and must be an **absolute URL** that already includes `/api`.

Example:

- `VITE_API_BASE_URL=https://super-marks-2-backend.vercel.app/api`

The frontend no longer relies on same-origin `/api` proxy functions.

## Vercel deployment

- Root Directory: `frontend`
- Build Command: `npm run build`
- Output Directory: `dist`
- SPA fallback routing is configured by `frontend/vercel.json`
