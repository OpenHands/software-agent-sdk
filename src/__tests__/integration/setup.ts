/**
 * Integration test setup
 *
 * This file runs before all integration tests.
 */

import { getTestConfig, skipIfNoConfig } from './test-config';
import * as fs from 'fs';

beforeAll(async () => {
  if (skipIfNoConfig()) {
    console.warn(
      '\n' +
        '⚠️  Integration tests skipped: LLM_API_KEY and LLM_MODEL environment variables not set.\n' +
        '\n' +
        'To run integration tests, set the following environment variables:\n' +
        '  - LLM_API_KEY: Your LLM provider API key\n' +
        '  - LLM_MODEL: The LLM model to use (e.g., anthropic/claude-sonnet-4-5-20250929)\n' +
        '  - AGENT_SERVER_URL: URL of the agent server (default: http://localhost:8010)\n' +
        '  - HOST_WORKSPACE_DIR: Path to mounted workspace on host (default: /tmp/agent-workspace)\n' +
        '\n'
    );
    return;
  }

  const config = getTestConfig();

  console.log('\n📦 Integration Test Configuration:');
  console.log(`   Agent Server URL: ${config.agentServerUrl}`);
  console.log(`   Agent Workspace Dir: ${config.agentWorkspaceDir}`);
  console.log(`   Host Workspace Dir: ${config.hostWorkspaceDir}`);
  console.log(`   LLM Model: ${config.llmModel}`);
  console.log(`   Test Timeout: ${config.testTimeout}ms\n`);

  // Ensure host workspace directory exists
  if (!fs.existsSync(config.hostWorkspaceDir)) {
    console.log(`Creating workspace directory: ${config.hostWorkspaceDir}`);
    fs.mkdirSync(config.hostWorkspaceDir, { recursive: true });
  }

  // Wait for agent server to be ready
  console.log('🔄 Waiting for agent server to be ready...');
  const maxRetries = 30;
  const retryDelay = 2000;

  for (let i = 0; i < maxRetries; i++) {
    try {
      const response = await fetch(`${config.agentServerUrl}/health`);
      if (response.ok) {
        console.log('✅ Agent server is ready!\n');
        return;
      }
    } catch (error) {
      // Server not ready yet
    }

    if (i < maxRetries - 1) {
      console.log(`   Retry ${i + 1}/${maxRetries}...`);
      await new Promise((resolve) => setTimeout(resolve, retryDelay));
    }
  }

  throw new Error(
    `Agent server not ready after ${maxRetries} retries. ` +
      `Make sure the agent-server is running at ${config.agentServerUrl}`
  );
});

afterAll(async () => {
  console.log('\n🧹 Integration tests completed.\n');
});
