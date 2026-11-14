import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Dev: `npm run dev` => http://localhost:5173, /api backend (uvicorn :8000) par proxy.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false },
    },
  },
  build: { sourcemap: false, chunkSizeWarningLimit: 600 },
});
