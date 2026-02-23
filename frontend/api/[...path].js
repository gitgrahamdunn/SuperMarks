export default async function handler(req, res) {
  try {
    const backend = (process.env.BACKEND_ORIGIN || "https://super-marks-2-backend.vercel.app").replace(/\/+$/, "");
    const url = new URL(req.url, "https://dummy.local");
    const pathParts = Array.isArray(req.query.path) ? req.query.path : [req.query.path].filter(Boolean);
    const subPath = pathParts.join("/");

    // special case: openapi.json should map to backend /openapi.json
    const targetBase =
      subPath === "openapi.json"
        ? `${backend}/openapi.json`
        : `${backend}/api/${subPath}`;

    const target = `${targetBase}${url.search || ""}`;

    // forward headers
    const headers = {};
    for (const [k, v] of Object.entries(req.headers || {})) {
      const key = k.toLowerCase();
      if (key === "host" || key === "connection" || key === "content-length") continue;
      headers[k] = v;
    }

    // read body (for POST/PUT/PATCH)
    let bodyBuf = null;
    if (!["GET", "HEAD"].includes(req.method || "GET")) {
      const chunks = [];
      for await (const chunk of req) chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
      bodyBuf = chunks.length ? Buffer.concat(chunks) : null;
    }

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
    res.setHeader("x-supermarks-proxy", "frontend-function");
    res.end(JSON.stringify({
      detail: "catch-all proxy failed",
      message: String(err?.message || err),
      name: String(err?.name || "Error")
    }));
  }
}
