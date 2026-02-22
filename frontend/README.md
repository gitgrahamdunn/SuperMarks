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

Do not set `VITE_API_BASE_URL` in production. The app should use same-origin `/api`, which is handled by the Vercel serverless proxy functions in `frontend/api/*.js`.

Set `BACKEND_ORIGIN` on the frontend Vercel project to your backend production URL (for example `https://super-marks-2-backend.vercel.app`). If not set, the proxy falls back to `https://super-marks-2-backend.vercel.app`.

## Vercel deployment

- Root Directory: `frontend`
- Build Command: `npm run build`
- Output Directory: `dist`
- SPA fallback routing is configured by `frontend/vercel.json`
