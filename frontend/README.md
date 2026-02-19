# SuperMarks Frontend

React + Vite frontend for SuperMarks, deployed as a dedicated Vercel project.

## Local development

```bash
cd frontend
npm install
npm run dev
```

The frontend uses `VITE_API_BASE_URL` when set. For local development it defaults to `http://localhost:8000`.

## Production configuration

Set the following in Vercel (or provide it in `.env.production`):

```env
VITE_API_BASE_URL=https://REPLACE_WITH_BACKEND_PROJECT_URL
```

## Vercel deployment

- Root Directory: `frontend`
- Build Command: `npm run build`
- Output Directory: `dist`
- SPA fallback routing is configured by `frontend/vercel.json`
