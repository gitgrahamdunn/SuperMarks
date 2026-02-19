# SuperMarks Frontend

Vite + React + TypeScript frontend for SuperMarks.

## Setup

1. Install dependencies:

```bash
npm install
```

2. Configure backend URL:

```bash
cp .env.example .env
```

Set `VITE_API_BASE_URL` in `.env` (default in example: `http://localhost:8000`).

3. Start dev server:

```bash
npm run dev
```

Frontend default URL: `http://localhost:5173`.

## Deploy to Vercel (separate project)

- Set project root directory to `frontend/`.
- Keep `frontend/vercel.json` checked in for SPA rewrites.
- Set `VITE_API_BASE_URL` to your deployed backend URL.

This prevents route-refresh 404s and ensures API calls target the backend deployment.
