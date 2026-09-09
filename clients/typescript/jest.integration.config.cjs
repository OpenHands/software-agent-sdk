/**
 * Jest configuration for integration tests
 *
 * These tests require a running agent-server in Docker with a mounted workspace.
 * Run with: npm run test:integration
 */
module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  roots: ['<rootDir>/src'],
  testMatch: [
    '**/__tests__/integration/**/*.integration.test.ts'
  ],
  transform: {
    '^.+\\.ts$': 'ts-jest',
    // Transform ESM modules from @openrouter/sdk
    '^.+\\.js$': 'babel-jest'
  },
  // Don't ignore @openrouter/sdk for transformation
  transformIgnorePatterns: [
    '/node_modules/(?!(@openrouter/sdk)/)'
  ],
  moduleFileExtensions: [
    'ts',
    'js',
    'json'
  ],
  // Integration tests need longer timeouts
  testTimeout: 180000,
  // Run tests serially to avoid conflicts
  maxWorkers: 1,
  // Verbose output for debugging
  verbose: true,
  // Setup files
  setupFilesAfterEnv: ['<rootDir>/src/__tests__/integration/setup.ts'],
  // Force exit after all tests complete to handle WebSocket connections
  // that may not be fully closed
  forceExit: true,
};
