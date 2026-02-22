export default async function handler(req, res) {
  res.statusCode = 200;
  res.setHeader("content-type", "application/json");
  res.setHeader("x-supermarks-proxy", "frontend-function");
  res.end(
    JSON.stringify({
      ok: true,
      method: req.method,
      url: req.url,
      hasApiKeyHeader: Boolean(req.headers["x-api-key"] || req.headers["X-API-Key"]),
      contentType: req.headers["content-type"] || null,
    })
  );
}
