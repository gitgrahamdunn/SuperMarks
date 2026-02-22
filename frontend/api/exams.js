const { proxy } = require('./_proxy');

module.exports = async (req, res) => {
  const backend = process.env.BACKEND_ORIGIN || 'https://super-marks-2-backend.vercel.app';
  const query = req.url.includes('?') ? req.url.slice(req.url.indexOf('?')) : '';
  const target = `${backend}/api/exams${query}`;
  await proxy(req, res, target);
};
