import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const backend = process.env.VITE_BACKEND_URL || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // REST + the /api/voice/ws WebSocket
      '/api': { target: backend, changeOrigin: true, ws: true },
    },
  },
})
