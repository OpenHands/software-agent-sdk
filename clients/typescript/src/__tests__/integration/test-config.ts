/**
 * Integration test configuration
 */

export interface ServerTestConfig {
  agentServerUrl: string;
  agentWorkspaceDir: string;
  hostWorkspaceDir: string;
  testTimeout: number;
}

export interface TestConfig extends ServerTestConfig {
  llmModel: string;
  llmApiKey: string;
  llmBaseUrl?: string;
}

export function getServerTestConfig(): ServerTestConfig {
  return {
    agentServerUrl: process.env.AGENT_SERVER_URL || 'http://localhost:8010',
    agentWorkspaceDir: process.env.AGENT_WORKSPACE_DIR || '/workspace',
    hostWorkspaceDir: process.env.HOST_WORKSPACE_DIR || '/tmp/agent-workspace',
    testTimeout: parseInt(process.env.TEST_TIMEOUT || '120000', 10),
  };
}

export function getTestConfig(): TestConfig {
  const llmApiKey = process.env.LLM_API_KEY;
  const llmModel = process.env.LLM_MODEL;

  if (!llmApiKey) {
    throw new Error(
      'LLM_API_KEY environment variable is required. ' +
        'Set it to your LLM provider API key (e.g., Anthropic, OpenAI).'
    );
  }

  if (!llmModel) {
    throw new Error(
      'LLM_MODEL environment variable is required. ' +
        'Set it to the model name (e.g., "anthropic/claude-sonnet-4-5-20250929").'
    );
  }

  return {
    ...getServerTestConfig(),
    llmModel,
    llmApiKey,
    llmBaseUrl: process.env.LLM_BASE_URL,
  };
}

export function skipIfNoConfig(): boolean {
  try {
    getTestConfig();
    return false;
  } catch {
    return true;
  }
}

export function createTestLLMConfig() {
  const config = getTestConfig();
  return {
    model: config.llmModel,
    api_key: config.llmApiKey,
    ...(config.llmBaseUrl && { base_url: config.llmBaseUrl }),
  };
}
