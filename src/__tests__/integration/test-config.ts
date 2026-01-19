/**
 * Integration test configuration
 *
 * Tests assume an agent-server is running inside a Docker container
 * with a volume mounted as the agent's workspace.
 *
 * Environment variables:
 * - AGENT_SERVER_URL: URL of the agent server (default: http://localhost:8010)
 * - AGENT_WORKSPACE_DIR: Path to the mounted workspace inside the container (default: /workspace)
 * - HOST_WORKSPACE_DIR: Path to the mounted workspace on the host (default: /tmp/agent-workspace)
 * - LLM_MODEL: LLM model to use (required, e.g., 'anthropic/claude-sonnet-4-5-20250929')
 * - LLM_API_KEY: API key for the LLM provider (required)
 * - LLM_BASE_URL: Optional base URL for LLM API
 */

export interface TestConfig {
  agentServerUrl: string;
  agentWorkspaceDir: string;
  hostWorkspaceDir: string;
  llmModel: string;
  llmApiKey: string;
  llmBaseUrl?: string;
  testTimeout: number;
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
    agentServerUrl: process.env.AGENT_SERVER_URL || 'http://localhost:8010',
    agentWorkspaceDir: process.env.AGENT_WORKSPACE_DIR || '/workspace',
    hostWorkspaceDir: process.env.HOST_WORKSPACE_DIR || '/tmp/agent-workspace',
    llmModel,
    llmApiKey,
    llmBaseUrl: process.env.LLM_BASE_URL,
    testTimeout: parseInt(process.env.TEST_TIMEOUT || '120000', 10),
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
