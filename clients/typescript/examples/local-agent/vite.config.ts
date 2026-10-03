import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@openhands/typescript-client': path.resolve(__dirname, '../../dist'),
    },
  },
  server: {
    port: 12001,
    host: true,
    allowedHosts: true,
  },
});
