const { request } = require('undici');

function sanitizeHeaders(h) {
  const out = {};
  for (const [k, v] of Object.entries(h || {})) {
    const key = k.toLowerCase();
    if (key === 'host' || key === 'connection' || key === 'content-length') continue;
    out[k] = v;
  }
  return out;
}

async function readRawBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  return chunks.length ? Buffer.concat(chunks) : null;
}

module.exports = async function proxy(req, res, targetUrl) {
  try {
    const method = req.method || 'GET';
    const headers = sanitizeHeaders(req.headers);

    const body = method === 'GET' || method === 'HEAD' ? null : await readRawBody(req);

    console.log('[proxy]', method, req.url, '->', targetUrl);

    const r = await request(targetUrl, {
      method,
      headers,
      body: body || undefined,
      headersTimeout: 30000,
      bodyTimeout: 30000,
      maxRedirections: 0,
    });

    res.statusCode = r.statusCode;

    for (const [k, v] of Object.entries(r.headers)) {
      if (v == null) continue;
      const key = k.toLowerCase();
      if (key === 'transfer-encoding') continue;
      res.setHeader(k, v);
    }

    const buf = Buffer.from(await r.body.arrayBuffer());
    res.end(buf);
  } catch (err) {
    console.error('[proxy-error]', err && (err.stack || err));
    res.statusCode = 502;
    res.setHeader('content-type', 'application/json');
    res.end(
      JSON.stringify({
        detail: 'Frontend proxy failed',
        message: String(err && err.message ? err.message : err),
        name: String(err && err.name ? err.name : 'Error'),
        target: targetUrl,
      }),
    );
  }
};
