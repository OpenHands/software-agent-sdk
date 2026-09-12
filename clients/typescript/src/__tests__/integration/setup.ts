/**
 * Integration test setup
 *
 * This file runs before all integration tests.
 */

import { getServerTestConfig, skipIfNoConfig, getTestConfig } from './test-config';
import * as fs from 'fs';

beforeAll(async () => {
  const serverConfig = getServerTestConfig();

  console.log('\n📦 Integration Test Configuration:');
  console.log(`   Agent Server URL: ${serverConfig.agentServerUrl}`);
  console.log(`   Agent Workspace Dir: ${serverConfig.agentWorkspaceDir}`);
  console.log(`   Host Workspace Dir: ${serverConfig.hostWorkspaceDir}`);
  console.log(`   Test Timeout: ${serverConfig.testTimeout}ms`);

  if (skipIfNoConfig()) {
    console.log('   LLM-backed tests: disabled (LLM_API_KEY / LLM_MODEL not set)\n');
  } else {
    const config = getTestConfig();
    console.log(`   LLM Model: ${config.llmModel}\n`);
  }

  if (!fs.existsSync(serverConfig.hostWorkspaceDir)) {
    console.log(`Creating workspace directory: ${serverConfig.hostWorkspaceDir}`);
    fs.mkdirSync(serverConfig.hostWorkspaceDir, { recursive: true });
  }

  console.log('🔄 Waiting for agent server to be ready...');
  const maxRetries = 30;
  const retryDelay = 2000;

  for (let i = 0; i < maxRetries; i++) {
    try {
      const response = await fetch(`${serverConfig.agentServerUrl}/health`);
      if (response.ok) {
        console.log('✅ Agent server is ready!\n');
        return;
      }
    } catch {
      // Server not ready yet
    }

    if (i < maxRetries - 1) {
      console.log(`   Retry ${i + 1}/${maxRetries}...`);
      await new Promise((resolve) => setTimeout(resolve, retryDelay));
    }
  }

  throw new Error(
    `Agent server not ready after ${maxRetries} retries. ` +
      `Make sure the agent-server is running at ${serverConfig.agentServerUrl}`
  );
});

afterAll(async () => {
  console.log('\n🧹 Integration tests completed.\n');
});
