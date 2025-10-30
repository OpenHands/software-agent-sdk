import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 12000,
    cors: true,
    allowedHosts: ['work-1-hzoqfnvxjubvyjbc.prod-runtime.all-hands.dev'],
    headers: {
      'X-Frame-Options': 'ALLOWALL',
    },
  },
  preview: {
    host: '0.0.0.0',
    port: 12000,
    cors: true,
    headers: {
      'X-Frame-Options': 'ALLOWALL',
    },
  },
})