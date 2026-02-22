module.exports = async (req, res) => {
  const backend = process.env.BACKEND_ORIGIN || 'https://super-marks-2-backend.vercel.app';
  const pathParts = Array.isArray(req.query.path) ? req.query.path : [req.query.path].filter(Boolean);
  const subPath = pathParts.join('/');

  if (subPath === 'proxy-health') {
    res.setHeader('content-type', 'application/json');
    res.status(200).send(JSON.stringify({ ok: true, backend }));
    return;
  }

  const target =
    subPath === 'openapi.json'
      ? `${backend}/openapi.json`
      : `${backend}/api/${subPath}${req.url.includes('?') ? req.url.slice(req.url.indexOf('?')) : ''}`;

  const headers = { ...req.headers };
  delete headers.host;
  delete headers.connection;
  delete headers['content-length'];

  const chunks = [];
  req.on('data', (c) => chunks.push(c));
  await new Promise((resolve) => req.on('end', resolve));
  const body = chunks.length ? Buffer.concat(chunks) : undefined;

  const fetchResp = await fetch(target, {
    method: req.method,
    headers,
    body: ['GET', 'HEAD'].includes(req.method) ? undefined : body,
  });

  res.status(fetchResp.status);
  fetchResp.headers.forEach((v, k) => {
    if (k.toLowerCase() === 'transfer-encoding') return;
    res.setHeader(k, v);
  });

  const buf = Buffer.from(await fetchResp.arrayBuffer());
  res.send(buf);
};
