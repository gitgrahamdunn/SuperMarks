async function readBodyBuffer(req) {
  const chunks = [];
  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }

  return chunks.length > 0 ? Buffer.concat(chunks) : undefined;
}

async function proxy(req, res, targetUrl) {
  const headers = { ...req.headers };
  delete headers.host;
  delete headers.connection;
  delete headers['content-length'];

  const body = await readBodyBuffer(req);
  const upstreamResponse = await fetch(targetUrl, {
    method: req.method,
    headers,
    body: ['GET', 'HEAD'].includes(req.method || '') ? undefined : body,
  });

  res.status(upstreamResponse.status);
  upstreamResponse.headers.forEach((value, key) => {
    if (key.toLowerCase() === 'transfer-encoding') {
      return;
    }
    res.setHeader(key, value);
  });

  const responseBody = Buffer.from(await upstreamResponse.arrayBuffer());
  res.send(responseBody);
}

module.exports = { proxy };
