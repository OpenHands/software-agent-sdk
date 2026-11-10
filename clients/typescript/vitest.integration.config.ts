import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    globals: true,
    environment: 'node',
    include: ['src/__tests__/integration/**/*.integration.test.ts'],
    setupFiles: ['src/__tests__/integration/setup.ts'],
    testTimeout: 180_000,
    maxWorkers: 1,
    fileParallelism: false,
  },
});
