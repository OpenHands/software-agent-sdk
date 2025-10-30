import { Settings } from '../components/SettingsModal';

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
 * Check if the agent server is reachable
 */
export const checkServerHealth = async (serverUrl: string, apiKey?: string): Promise<{ isConnected: boolean; error?: string }> => {
  try {
    const url = `${serverUrl.replace(/\/$/, '')}/health`;
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    
    if (apiKey) {
      headers['X-Session-API-Key'] = apiKey;
    }

    const response = await fetch(url, {
      method: 'GET',
      headers,
      signal: AbortSignal.timeout(5000), // 5 second timeout
    });

    if (response.ok) {
      return { isConnected: true };
    } else {
      return { 
        isConnected: false, 
        error: `Server responded with ${response.status}: ${response.statusText}` 
      };
    }
  } catch (error) {
    if (error instanceof Error) {
      if (error.name === 'AbortError') {
        return { isConnected: false, error: 'Connection timeout' };
      }
      return { isConnected: false, error: error.message };
    }
    return { isConnected: false, error: 'Unknown connection error' };
  }
};

/**
 * Test LLM configuration with a simple query
 */
export const testLLMConfiguration = async (settings: Settings): Promise<{ success: boolean; error?: string }> => {
  try {
    // First check if server is reachable
    const healthCheck = await checkServerHealth(settings.agentServerUrl, settings.agentServerApiKey);
    if (!healthCheck.isConnected) {
      return { success: false, error: `Server not reachable: ${healthCheck.error}` };
    }

    // Create a test conversation to validate LLM settings
    const createUrl = `${settings.agentServerUrl.replace(/\/$/, '')}/api/conversations`;
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    
    if (settings.agentServerApiKey) {
      headers['X-Session-API-Key'] = settings.agentServerApiKey;
    }

    const createResponse = await fetch(createUrl, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        agent: {
          name: 'TestAgent',
          llm: {
            model: settings.modelName,
            api_key: settings.apiKey,
          }
        },
        workspace: {
          type: 'local',
          path: '/tmp/test-workspace'
        }
      }),
      signal: AbortSignal.timeout(10000), // 10 second timeout
    });

    if (!createResponse.ok) {
      const errorText = await createResponse.text();
      return { 
        success: false, 
        error: `Failed to create test conversation: ${createResponse.status} ${errorText}` 
      };
    }

    const conversationData = await createResponse.json();
    const conversationId = conversationData.conversation_id;

    try {
      // Send a simple test message
      const messageUrl = `${settings.agentServerUrl.replace(/\/$/, '')}/api/conversations/${conversationId}/messages`;
      const messageResponse = await fetch(messageUrl, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          role: 'user',
          content: [{ type: 'text', text: 'Hello, respond with just "OK" to confirm you are working.' }],
          run: false
        }),
        signal: AbortSignal.timeout(15000), // 15 second timeout
      });

      if (messageResponse.ok) {
        // Clean up the test conversation
        try {
          await fetch(`${settings.agentServerUrl.replace(/\/$/, '')}/api/conversations/${conversationId}`, {
            method: 'DELETE',
            headers,
          });
        } catch {
          // Ignore cleanup errors
        }
        
        return { success: true };
      } else {
        const errorText = await messageResponse.text();
        return { 
          success: false, 
          error: `LLM test failed: ${messageResponse.status} ${errorText}` 
        };
      }
    } catch (testError) {
      // Clean up the test conversation even if test failed
      try {
        await fetch(`${settings.agentServerUrl.replace(/\/$/, '')}/api/conversations/${conversationId}`, {
          method: 'DELETE',
          headers,
        });
      } catch {
        // Ignore cleanup errors
      }
      throw testError;
    }
  } catch (error) {
    if (error instanceof Error) {
      if (error.name === 'AbortError') {
        return { success: false, error: 'LLM test timeout' };
      }
      return { success: false, error: `LLM test error: ${error.message}` };
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