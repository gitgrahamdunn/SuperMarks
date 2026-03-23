import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const buildMarker = String(Date.now());

function resolveAllowedHosts(): true | string[] {
  const configuredHosts = (process.env.VITE_ALLOWED_HOSTS || '')
    .split(',')
    .map((host) => host.trim())
    .filter(Boolean);

  if (configuredHosts.includes('*')) {
    return true;
  }

  if (configuredHosts.length > 0) {
    return configuredHosts;
  }

  return ['localhost', '127.0.0.1', 'test-mpg-h510-trident-3-ms-b935.taildeec39.ts.net'];
}

export default defineConfig({
  plugins: [
    react(),
    {
      name: 'supermarks-build-marker',
      transformIndexHtml(html) {
        return html.replace(
          '</head>',
          `    <meta name="supermarks-build" content="${buildMarker}" />\n  </head>`,
        );
      },
    },
  ],
  server: {
    allowedHosts: resolveAllowedHosts(),
    proxy: {
      '/openapi.json': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/version': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  define: {
    __APP_BUILD_TS__: JSON.stringify(Number(buildMarker)),
  },
});
