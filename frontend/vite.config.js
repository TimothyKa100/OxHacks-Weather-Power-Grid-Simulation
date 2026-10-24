import {defineConfig} from 'vite';

// The Flask API runs on 5001 (see ../README.md). Both the dev server and
// `vite preview` proxy /api there, so the UI never needs CORS.
const proxy = {'/api': 'http://127.0.0.1:5001'};

export default defineConfig({
  server: {
    proxy,
    // The offline fallback imports ../data/sample_storm.json from the repo root.
    fs: {allow: ['..']},
  },
  preview: {proxy},
  // The largest chunk is the offline sample storm (~0.8 MB), loaded only when the backend is down.
  build: {outDir: 'dist', chunkSizeWarningLimit: 900},
});
