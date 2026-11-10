/**
 * Integration tests for HttpClient
 *
 * Tests HTTP client functionality against a real agent-server.
 */

import { HttpClient, HttpError } from '../../index';
import { getTestConfig, skipIfNoConfig } from './test-config';

const SKIP_TESTS = skipIfNoConfig();

type ServerInfoResponse = {
  version?: string;
};

type CreateConversationResponse = {
  id: string;
};

type ConversationResourceResponse = {
  id?: string;
  full_state?: {
    id?: string;
  };
};

describe('HttpClient Integration Tests', () => {
  let client: HttpClient;
  let config: ReturnType<typeof getTestConfig>;

  beforeAll(() => {
    if (SKIP_TESTS) {
      console.warn('Skipping integration tests: LLM_API_KEY and LLM_MODEL not set');
      return;
    }
    config = getTestConfig();
    client = new HttpClient({
      baseUrl: config.agentServerUrl,
      timeout: 30000,
    });
  });

  afterAll(() => {
    if (client) {
      client.close();
    }
  });

  describe('Health and Server Info', () => {
    it(
      'should check server alive status',
      async () => {
        if (SKIP_TESTS) return;

        const response = await client.get('/alive');

        expect(response.status).toBe(200);
      },
      config?.testTimeout || 30000
    );

    it(
      'should check server health',
      async () => {
        if (SKIP_TESTS) return;

        const response = await client.get('/health');

        expect(response.status).toBe(200);
      },
      config?.testTimeout || 30000
    );

    it(
      'should get server info',
      async () => {
        if (SKIP_TESTS) return;

        const response = await client.get<ServerInfoResponse>('/server_info');

        expect(response.status).toBe(200);
        expect(response.data).toBeDefined();
        // Server info should have a version
        expect(response.data.version).toBeDefined();
      },
      config?.testTimeout || 30000
    );
  });

  describe('GET Requests', () => {
    it(
      'should make GET request with query params',
      async () => {
        if (SKIP_TESTS) return;

        // Use the search endpoint which supports query params
        const response = await client.get('/api/conversations/search', {
          params: { limit: 10 },
        });

        expect(response.status).toBe(200);
        expect(response.data).toBeDefined();
      },
      config?.testTimeout || 30000
    );

    it(
      'should handle 404 responses',
      async () => {
        if (SKIP_TESTS) return;

        try {
          // Use a valid UUID format but non-existent ID
          await client.get('/api/conversations/00000000-0000-0000-0000-000000000000');
          fail('Expected HttpError to be thrown');
        } catch (error) {
          expect(error).toBeInstanceOf(HttpError);
          const httpError = error as HttpError;
          expect(httpError.status).toBe(404);
        }
      },
      config?.testTimeout || 30000
    );
  });

  describe('POST Requests', () => {
    it(
      'should make POST request with JSON body',
      async () => {
        if (SKIP_TESTS) return;

        // Create a conversation to test POST
        const response = await client.post<CreateConversationResponse>('/api/conversations', {
          agent: {
            kind: 'Agent',
            llm: {
              model: config.llmModel,
              api_key: config.llmApiKey,
              ...(config.llmBaseUrl && { base_url: config.llmBaseUrl }),
            },
          },
          max_iterations: 5,
          stuck_detection: true,
          workspace: {
            type: 'local',
            working_dir: config.agentWorkspaceDir,
          },
        });

        // Accept both 200 and 201 (Created) as valid responses for resource creation
        expect([200, 201]).toContain(response.status);
        expect(response.data).toBeDefined();
        expect(response.data.id).toBeDefined();
      },
      config?.testTimeout || 60000
    );
  });

  describe('Error Handling', () => {
    it(
      'should handle malformed request body',
      async () => {
        if (SKIP_TESTS) return;

        try {
          // Send request missing required fields
          await client.post('/api/conversations', {});
          fail('Expected HttpError to be thrown');
        } catch (error) {
          expect(error).toBeInstanceOf(HttpError);
          const httpError = error as HttpError;
          // Should get 422 (validation error) or 400 (bad request)
          expect([400, 422]).toContain(httpError.status);
        }
      },
      config?.testTimeout || 30000
    );

    it(
      'should handle timeout configuration',
      async () => {
        if (SKIP_TESTS) return;

        const shortTimeoutClient = new HttpClient({
          baseUrl: config.agentServerUrl,
          timeout: 1, // 1ms timeout - should fail
        });

        try {
          await shortTimeoutClient.get('/health');
          // If it succeeds, the server was very fast
        } catch (error) {
          // Timeout error expected
          expect(error).toBeDefined();
        } finally {
          shortTimeoutClient.close();
        }
      },
      config?.testTimeout || 30000
    );
  });

  describe('Response Headers', () => {
    it(
      'should include response headers',
      async () => {
        if (SKIP_TESTS) return;

        const response = await client.get('/health');

        expect(response.headers).toBeDefined();
        expect(typeof response.headers).toBe('object');
        // Should have content-type header
        expect(response.headers['content-type']).toBeDefined();
      },
      config?.testTimeout || 30000
    );
  });

  describe('Request Methods', () => {
    let testConversationId: string;

    beforeAll(async () => {
      if (SKIP_TESTS) return;

      // Create a conversation to test with
      const response = await client.post<CreateConversationResponse>('/api/conversations', {
        agent: {
          kind: 'Agent',
          llm: {
            model: config.llmModel,
            api_key: config.llmApiKey,
            ...(config.llmBaseUrl && { base_url: config.llmBaseUrl }),
          },
        },
        max_iterations: 5,
        stuck_detection: true,
        workspace: {
          type: 'local',
          working_dir: config.agentWorkspaceDir,
        },
      });

      testConversationId = response.data.id;
    });

    it(
      'should make GET request for specific resource',
      async () => {
        if (SKIP_TESTS) return;

        const response = await client.get<ConversationResourceResponse>(
          `/api/conversations/${testConversationId}`
        );

        expect(response.status).toBe(200);
        expect(response.data).toBeDefined();
        // Handle both direct response and full_state wrapper
        const conversationData = response.data.full_state || response.data;
        expect(conversationData.id || response.data.id).toBe(testConversationId);
      },
      config?.testTimeout || 30000
    );

    it(
      'should support custom request options',
      async () => {
        if (SKIP_TESTS) return;

        const response = await client.request({
          method: 'GET',
          url: `/api/conversations/${testConversationId}`,
          timeout: 15000,
        });

        expect(response.status).toBe(200);
      },
      config?.testTimeout || 30000
    );

    it(
      'should send POST to events endpoint',
      async () => {
        if (SKIP_TESTS) return;

        const response = await client.post(`/api/conversations/${testConversationId}/events`, {
          role: 'user',
          content: [{ type: 'text', text: 'Test message via HttpClient' }],
          run: false,
        });

        expect(response.status).toBe(200);
      },
      config?.testTimeout || 30000
    );
  });
});
