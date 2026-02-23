function sanitizeHeaders(headers) {
  const forwarded = {};

  for (const [name, value] of Object.entries(headers || {})) {
    const lowerName = name.toLowerCase();
    if (lowerName === 'host' || lowerName === 'connection' || lowerName === 'content-length') {
      continue;
    }
    forwarded[name] = value;
  }

  return forwarded;
}

async function readRequestBody(req, method) {
  if (!method || method === 'GET' || method === 'HEAD') {
    return undefined;
  }

  const chunks = [];
  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }

  if (!chunks.length) {
    return undefined;
  }

  return Buffer.concat(chunks);
}

function resolveTargetUrl(req, backendOrigin) {
  const requestUrl = new URL(req.url, 'https://proxy.local');
  const pathParam = req.query?.path;
  const pathSegments = Array.isArray(pathParam) ? pathParam : [pathParam].filter(Boolean);
  const subPath = pathSegments.join('/');

  if (subPath.startsWith('exams-create')) {
    return null;
  }

  const baseUrl = subPath === 'openapi.json'
    ? `${backendOrigin}/openapi.json`
    : `${backendOrigin}/api/${subPath}`;

  return `${baseUrl}${requestUrl.search || ''}`;
}

export default async function handler(req, res) {
  try {
    const backendOrigin = (process.env.BACKEND_ORIGIN || 'https://super-marks-2-backend.vercel.app').replace(/\/+$/, '');
    const targetUrl = resolveTargetUrl(req, backendOrigin);

    if (!targetUrl) {
      res.statusCode = 404;
      res.setHeader('content-type', 'application/json');
      res.end(JSON.stringify({ detail: 'Route handled by /api/exams-create.' }));
      return;
    }

    const response = await fetch(targetUrl, {
      method: req.method,
      headers: sanitizeHeaders(req.headers),
      body: await readRequestBody(req, req.method),
      redirect: 'manual',
    });

    res.statusCode = response.status;
    response.headers.forEach((value, key) => {
      if (key.toLowerCase() === 'transfer-encoding') {
        return;
      }
      res.setHeader(key, value);
    });

    res.setHeader('x-supermarks-proxy', 'frontend-function');
    res.end(Buffer.from(await response.arrayBuffer()));
  } catch (error) {
    res.statusCode = 502;
    res.setHeader('content-type', 'application/json');
    res.setHeader('x-supermarks-proxy', 'frontend-function');
    res.end(
      JSON.stringify({
        detail: 'catch-all proxy failed',
        message: String(error?.message || error),
        name: String(error?.name || 'Error'),
      }),
    );
  }
}
