/*
IMPORTANT:
This file implements Strategy A API routing.
Do not add Vercel rewrites for /api.
See docs/ARCHITECTURE.md.
*/

export default async function handler(req, res) {
  try {
    const backend = (process.env.BACKEND_ORIGIN || 'https://super-marks-2-backend.vercel.app').replace(/\/+$/, '');
    const url = new URL(req.url, 'https://proxy.local');
    const name = (url.searchParams.get('name') || '').trim() || `Exam ${Date.now()}`;

    const apiKey = req.headers['x-api-key'] || req.headers['X-API-Key'] || null;

    const response = await fetch(`${backend}/api/exams`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(apiKey ? { 'X-API-Key': apiKey } : {}),
      },
      body: JSON.stringify({ name }),
    });

    const bodyText = await response.text();
    res.statusCode = response.status;
    res.setHeader('content-type', response.headers.get('content-type') || 'application/json');
    res.setHeader('x-supermarks-proxy', 'repo-root-function');
    res.end(bodyText);
  } catch (error) {
    res.statusCode = 502;
    res.setHeader('content-type', 'application/json');
    res.setHeader('x-supermarks-proxy', 'repo-root-function');
    res.end(
      JSON.stringify({
        detail: 'exams-create proxy failed',
        message: String(error?.message || error),
        name: String(error?.name || 'Error'),
      }),
    );
  }
}
