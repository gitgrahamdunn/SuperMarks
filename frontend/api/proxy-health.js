module.exports = async (_req, res) => {
  const backend = process.env.BACKEND_ORIGIN || 'https://super-marks-2-backend.vercel.app';
  res.statusCode = 200;
  res.setHeader('content-type', 'application/json');
  res.end(JSON.stringify({ ok: true, backend }));
};
