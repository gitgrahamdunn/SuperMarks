export default async function handler(req, res) {
  try {
    const backend = process.env.BACKEND_ORIGIN || 'https://super-marks-2-backend.vercel.app';
    const url = new URL(req.url, 'https://dummy.local');
    const name = (url.searchParams.get('name') || '').trim() || `Exam ${Date.now()}`;

    const apiKey = req.headers['x-api-key'] || req.headers['X-API-Key'] || null;

    const resp = await fetch(`${backend}/api/exams`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(apiKey ? { 'X-API-Key': apiKey } : {}),
      },
      body: JSON.stringify({ name }),
    });

    const text = await resp.text();
    res.statusCode = resp.status;
    res.setHeader('content-type', resp.headers.get('content-type') || 'application/json');
    res.setHeader('x-supermarks-proxy', 'frontend-function');
    res.end(text);
  } catch (err) {
    res.statusCode = 502;
    res.setHeader('content-type', 'application/json');
    res.setHeader('x-supermarks-proxy', 'frontend-function');
    res.end(JSON.stringify({
      detail: 'exams-create proxy failed',
      message: String(err?.message || err),
      name: String(err?.name || 'Error'),
    }));
  }
}
