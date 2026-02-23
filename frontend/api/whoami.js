export default async function handler(_req, res) {
  res.statusCode = 200;
  res.setHeader("content-type", "application/json");
  res.setHeader("x-supermarks-proxy", "whoami");
  res.end(JSON.stringify({ ok: true, handler: "whoami", ts: Date.now() }));
}
