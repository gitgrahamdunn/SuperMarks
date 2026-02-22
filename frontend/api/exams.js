async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  return chunks.length ? Buffer.concat(chunks) : null;
}

function sanitizeHeaders(headers) {
  const out = {};
  for (const [k, v] of Object.entries(headers || {})) {
    const key = k.toLowerCase();
    if (key === 'host' || key === 'connection' || key === 'content-length') continue;
    out[k] = v;
  }
  return out;
}

async function proxy(req, res, targetUrl) {
  try {
    const method = req.method || 'GET';
    const headers = sanitizeHeaders(req.headers);

    const body = method === 'GET' || method === 'HEAD' ? null : await readBody(req);

    const resp = await fetch(targetUrl, {
      method,
      headers,
      body: body ? body : undefined,
      redirect: 'manual',
    });

    res.statusCode = resp.status;

    resp.headers.forEach((value, key) => {
      if (key.toLowerCase() === 'transfer-encoding') return;
      res.setHeader(key, value);
    });
    res.setHeader('x-supermarks-proxy', 'frontend-function');

    const buf = Buffer.from(await resp.arrayBuffer());
    res.end(buf);
  } catch (err) {
    res.statusCode = 502;
    res.setHeader('content-type', 'application/json');
    res.setHeader('x-supermarks-proxy', 'frontend-function');
    res.end(
      JSON.stringify({
        detail: 'Frontend proxy failed',
        message: String(err?.message || err),
        name: String(err?.name || 'Error'),
        target: targetUrl,
      }),
    );
  }
}

export default async function handler(req, res) {
  const backend = process.env.BACKEND_ORIGIN || 'https://super-marks-2-backend.vercel.app';
  const qs = req.url.includes('?') ? req.url.slice(req.url.indexOf('?')) : '';
  const target = `${backend}/api/exams${qs}`;
  return proxy(req, res, target);
}
