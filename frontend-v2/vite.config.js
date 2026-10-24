import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';

// The Flask API runs on 5001 (see ../README.md). Both the dev server and
// `vite preview` proxy /api there, so the UI never needs CORS.
const proxy = {'/api': 'http://127.0.0.1:5001'};

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy,
    // The offline fallback imports ../data/sample_run.json from the repo root.
    fs: {allow: ['..']},
  },
  preview: {port: 4174, proxy},
  build: {outDir: 'dist'},
});
