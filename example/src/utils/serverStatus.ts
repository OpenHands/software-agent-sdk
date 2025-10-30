import { Settings } from '../components/SettingsModal';
import { HttpClient, RemoteConversation, RemoteWorkspace } from '@openhands/agent-server-typescript-client';

export interface ServerStatus {
  isConnected: boolean;
  connectionError?: string;
  llmStatus: 'unknown' | 'working' | 'error';
  llmError?: string;
  lastChecked: Date;
}

export interface HealthCheckResponse {
  status: string;
  timestamp: string;
  version?: string;
}

export interface LLMTestResponse {
  success: boolean;
  response?: string;
  error?: string;
}

/**
 * Check if the agent server is reachable using the SDK
 */
export const checkServerHealth = async (serverUrl: string, apiKey?: string): Promise<{ isConnected: boolean; error?: string }> => {
  try {
    const client = new HttpClient({ baseUrl: serverUrl, apiKey });
    
    // Use the SDK's HTTP client to check health
    await client.get('/health');
    
    return { isConnected: true };
  } catch (error) {
    if (error instanceof Error) {
      return { isConnected: false, error: error.message };
    }
    return { isConnected: false, error: 'Unknown connection error' };
  }
};

/**
 * Test LLM configuration using the SDK
 */
export const testLLMConfiguration = async (settings: Settings): Promise<{ success: boolean; error?: string }> => {
  try {
    // First check if server is reachable
    const healthCheck = await checkServerHealth(settings.agentServerUrl, settings.agentServerApiKey);
    if (!healthCheck.isConnected) {
      return { success: false, error: `Server not reachable: ${healthCheck.error}` };
    }

    // Create a workspace for the test conversation
    const workspace = new RemoteWorkspace({
      host: settings.agentServerUrl,
      workingDir: '/tmp/test-workspace',
      apiKey: settings.agentServerApiKey,
    });

    // Create a test conversation using the SDK
    const conversation = new RemoteConversation(
      {
        kind: 'Agent',
        llm: {
          model: settings.modelName,
          api_key: settings.apiKey,
        }
      },
      workspace
    );

    await conversation.start();

    try {
      // Send a simple test message to validate LLM configuration
      await conversation.sendMessage({
        role: 'user',
        content: [{ type: 'text', text: 'Hello, respond with just "OK" to confirm you are working.' }]
      });
      
      // If we get here, the LLM configuration is working
      return { success: true };
      
    } finally {
      // Always try to clean up the test conversation
      try {
        await conversation.close();
      } catch {
        // Ignore cleanup errors
      }
    }
  } catch (error) {
    if (error instanceof Error) {
      return { success: false, error: error.message };
    }
    return { success: false, error: 'Unknown LLM test error' };
  }
};

/**
 * Get comprehensive server status
 */
export const getServerStatus = async (settings: Settings): Promise<ServerStatus> => {
  const startTime = new Date();
  
  // Check server connection
  const healthCheck = await checkServerHealth(settings.agentServerUrl, settings.agentServerApiKey);
  
  let llmStatus: 'unknown' | 'working' | 'error' = 'unknown';
  let llmError: string | undefined;
  
  // Check if LLM settings are configured
  if (!settings.apiKey || !settings.modelName) {
    llmStatus = 'unknown';
    llmError = 'LLM API key or model name not configured';
  } else if (!healthCheck.isConnected) {
    // Settings are configured but server is not reachable
    llmStatus = 'unknown';
    llmError = 'Cannot test LLM configuration - server not reachable';
  } else {
    // Server is reachable and settings are configured - test LLM
    const llmTest = await testLLMConfiguration(settings);
    llmStatus = llmTest.success ? 'working' : 'error';
    llmError = llmTest.error;
  }

  return {
    isConnected: healthCheck.isConnected,
    connectionError: healthCheck.error,
    llmStatus,
    llmError,
    lastChecked: startTime,
  };
};