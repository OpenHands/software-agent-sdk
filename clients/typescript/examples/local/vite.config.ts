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
    port: 12000,
    host: true,
    allowedHosts: ['work-1-govhxshotncecgfj.prod-runtime.all-hands.dev'],
  },
});
