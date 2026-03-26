import { existsSync, readFileSync } from 'node:fs';

const failures = [];

const vercelConfigPath = new URL('../vercel.json', import.meta.url);
if (existsSync(vercelConfigPath)) {
  failures.push('frontend/vercel.json should not exist for the Cloudflare Pages frontend.');
}

const cloudflareRedirectsPath = new URL('../public/_redirects', import.meta.url);
if (!existsSync(cloudflareRedirectsPath)) {
  failures.push('frontend/public/_redirects is required for Cloudflare Pages SPA routing.');
} else {
  const redirects = readFileSync(cloudflareRedirectsPath, 'utf8');
  if (!redirects.includes('/* /index.html 200')) {
    failures.push('frontend/public/_redirects must include "/* /index.html 200".');
  }
}

const wranglerConfigPath = new URL('../wrangler.toml', import.meta.url);
if (!existsSync(wranglerConfigPath)) {
  failures.push('frontend/wrangler.toml is required for the Cloudflare Pages deployment path.');
} else {
  const wranglerConfig = readFileSync(wranglerConfigPath, 'utf8');
  if (!wranglerConfig.includes('pages_build_output_dir = "./dist"')) {
    failures.push('frontend/wrangler.toml must set pages_build_output_dir = "./dist".');
  }
}

const blockedFiles = [
  '../api/[...path].js',
  '../api/exams-create.js',
  '../api/proxy-health.js',
  '../../api/[...path].js',
  '../../api/exams-create.js',
  '../../api/proxy-health.js',
];

for (const relPath of blockedFiles) {
  if (existsSync(new URL(relPath, import.meta.url))) {
    failures.push(`Strategy A artifact still exists: ${relPath}`);
  }
}

if (failures.length > 0) {
  console.error('Strategy B smoke checks failed:');
  failures.forEach((failure) => console.error(`- ${failure}`));
  process.exit(1);
}

console.log('Strategy B smoke checks passed.');
