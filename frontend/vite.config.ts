import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_BUILD_TS__: JSON.stringify(Date.now()),
  },
});
