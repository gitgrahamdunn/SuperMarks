const proxy = require('./_proxy');

module.exports = async (req, res) => {
  const backend = process.env.BACKEND_ORIGIN || 'https://super-marks-2-backend.vercel.app';
  const qs = req.url.includes('?') ? req.url.slice(req.url.indexOf('?')) : '';
  const target = `${backend}/openapi.json${qs}`;
  return proxy(req, res, target);
};
