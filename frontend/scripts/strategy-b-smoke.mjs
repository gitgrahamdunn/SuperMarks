import { existsSync, readFileSync } from 'node:fs';

const failures = [];

const vercelConfig = JSON.parse(readFileSync(new URL('../vercel.json', import.meta.url), 'utf8'));
if (vercelConfig.rewrites) {
  failures.push('frontend/vercel.json must not define rewrites for Strategy B.');
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
