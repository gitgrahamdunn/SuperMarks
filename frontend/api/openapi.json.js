const { proxy } = require('./_proxy');

module.exports = async (req, res) => {
  const backend = process.env.BACKEND_ORIGIN || 'https://super-marks-2-backend.vercel.app';
  const target = `${backend}/openapi.json`;
  await proxy(req, res, target);
};
