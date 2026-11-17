import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const backend = { target: 'http://localhost:8000', changeOrigin: true }

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // /auth/callback is a React page (the backend redirects there with ?token=),
      // so only the backend-initiated auth routes are proxied.
      '/auth/github': backend,
      '/auth/me': backend,
      '/api': backend,
      '/webhook': backend,
      '/health': backend,
    },
  },
})
