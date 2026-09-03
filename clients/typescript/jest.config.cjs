module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  roots: ['<rootDir>/src'],
  testMatch: [
    // Only match files ending in .test.ts or .spec.ts
    '**/__tests__/**/*.test.ts',
    '**/__tests__/**/*.spec.ts',
    '**/?(*.)+(spec|test).ts'
  ],
  testPathIgnorePatterns: [
    '/node_modules/',
    // Ignore integration tests by default (run separately)
    '.*\\.integration\\.test\\.ts$',
    // Ignore helper files
    '.*/__tests__/integration/(test-config|test-utils|setup|index)\\.ts$'
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
  collectCoverageFrom: [
    'src/**/*.ts',
    '!src/**/*.d.ts',
    '!src/__tests__/**/*'
  ],
  coverageDirectory: 'coverage',
  coverageReporters: [
    'text',
    'lcov',
    'html'
  ],
  moduleFileExtensions: [
    'ts',
    'js',
    'json'
  ],
  testTimeout: 10000
};