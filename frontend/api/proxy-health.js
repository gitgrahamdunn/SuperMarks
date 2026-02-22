export default async function handler(_req, res) {
  const backend = process.env.BACKEND_ORIGIN || 'https://super-marks-2-backend.vercel.app';
  res.statusCode = 200;
  res.setHeader('content-type', 'application/json');
  res.setHeader('x-supermarks-proxy', 'frontend-function');
  res.end(JSON.stringify({ ok: true, backend }));
}
