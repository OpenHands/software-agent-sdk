module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  roots: ['<rootDir>/src'],
  testMatch: [
    '**/__tests__/**/*.ts',
    '**/?(*.)+(spec|test).ts'
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