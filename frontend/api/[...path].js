export default async function handler(req, res) {
  const backend = (process.env.BACKEND_ORIGIN || "https://super-marks-2-backend.vercel.app").replace(/\/+$/, "");

  // Handle OPTIONS locally (DO NOT FORWARD)
  if (req.method === "OPTIONS") {
    res.statusCode = 204;
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Access-Control-Allow-Methods", "GET,POST,PUT,PATCH,DELETE,OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type, X-API-Key, Authorization");
    res.end();
    return;
  }

  const url = new URL(req.url, "https://dummy.local");
  const parts = Array.isArray(req.query.path) ? req.query.path : [req.query.path].filter(Boolean);
  const subPath = parts.join("/");

  // Map special paths to backend root endpoints
  let targetBase;
  if (subPath === "health") targetBase = `${backend}/health`;
  else if (subPath === "openapi.json") targetBase = `${backend}/openapi.json`;
  else targetBase = `${backend}/api/${subPath}`;

  const target = `${targetBase}${url.search || ""}`;

  // forward headers
  const headers = {};
  for (const [k, v] of Object.entries(req.headers || {})) {
    const key = k.toLowerCase();
    if (key === "host" || key === "connection" || key === "content-length") continue;
    headers[k] = v;
  }

  // read body for non-GET/HEAD
  let bodyBuf = null;
  if (!["GET","HEAD"].includes(req.method || "GET")) {
    const chunks = [];
    for await (const chunk of req) chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    bodyBuf = chunks.length ? Buffer.concat(chunks) : null;
  }

  try {
    const resp = await fetch(target, {
      method: req.method,
      headers,
      body: bodyBuf ? bodyBuf : undefined,
      redirect: "manual"
    });

    res.statusCode = resp.status;
    resp.headers.forEach((value, key) => {
      if (key.toLowerCase() === "transfer-encoding") return;
      res.setHeader(key, value);
    });
    res.setHeader("x-supermarks-proxy", "frontend-function");

    const buf = Buffer.from(await resp.arrayBuffer());
    res.end(buf);
  } catch (err) {
    res.statusCode = 502;
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify({
      detail: "proxy fetch failed",
      message: String(err?.message || err),
      target
    }));
  }
}
