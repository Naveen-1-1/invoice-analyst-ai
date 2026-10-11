import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(import.meta.dirname, './src') } },
  server: {
    host: '127.0.0.1',
    port: 5180,
    // The API runs separately (make api); the dev server forwards /api so both share one origin
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
})
