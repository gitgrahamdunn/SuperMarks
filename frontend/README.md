# SuperMarks Frontend

React + Vite frontend for SuperMarks, deployed as a dedicated Vercel project.

## Local development

```bash
cd frontend
npm install
npm run dev
```

The frontend defaults to same-origin `/api`. Set `VITE_API_BASE_URL` only when you need a custom backend URL in local development (for example `http://localhost:8000/api`).

## Production configuration

Do not set `VITE_API_BASE_URL` in production. The app should use `/api`, which Vercel rewrites to the backend via `frontend/vercel.json`.

## Vercel deployment

- Root Directory: `frontend`
- Build Command: `npm run build`
- Output Directory: `dist`
- SPA fallback routing is configured by `frontend/vercel.json`
