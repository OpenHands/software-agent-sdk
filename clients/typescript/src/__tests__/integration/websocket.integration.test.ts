/**
 * Integration tests for WebSocketCallbackClient
 *
 * Tests real-time event streaming from the agent-server.
 *
 * NOTE: Some tests in this file are skipped by default because they depend on
 * LLM behavior which is non-deterministic and can be slow.
 */

import { WebSocketCallbackClient, HttpClient, Event } from '../../index';
import { getTestConfig, skipIfNoConfig, createTestLLMConfig } from './test-config';
import { sleep, waitFor } from './test-utils';

const SKIP_TESTS = skipIfNoConfig();
// Skip flaky tests that depend on LLM behavior in CI
const SKIP_FLAKY_TESTS = process.env.SKIP_FLAKY_TESTS !== 'false';

type CreateConversationResponse = {
  id: string;
};

describe('WebSocket Integration Tests', () => {
  let config: ReturnType<typeof getTestConfig>;
  let httpClient: HttpClient;

  beforeAll(() => {
    if (SKIP_TESTS) {
      console.warn('Skipping integration tests: LLM_API_KEY and LLM_MODEL not set');
      return;
    }
    config = getTestConfig();
    httpClient = new HttpClient({
      baseUrl: config.agentServerUrl,
      timeout: 60000,
    });
  });

  afterAll(() => {
    if (httpClient) {
      httpClient.close();
    }
  });

  describe('WebSocket Connection', () => {
    it(
      'should establish WebSocket connection and receive events',
      async () => {
        if (SKIP_TESTS) return;

        // Create a conversation first
        const createResponse = await httpClient.post<CreateConversationResponse>(
          '/api/conversations',
          {
            agent: {
              kind: 'Agent',
              llm: createTestLLMConfig(),
            },
            max_iterations: 10,
            stuck_detection: true,
            workspace: {
              type: 'local',
              working_dir: config.agentWorkspaceDir,
            },
          }
        );

        const conversationId = createResponse.data.id;
        const receivedEvents: Event[] = [];

        // Create WebSocket client
        const wsClient = new WebSocketCallbackClient({
          host: config.agentServerUrl,
          conversationId,
          callback: (event) => {
            receivedEvents.push(event);
          },
        });

        // Start the WebSocket connection
        wsClient.start();

        // Wait for connection to establish
        await sleep(1000);

        // Send a message and run the agent
        await httpClient.post(`/api/conversations/${conversationId}/events`, {
          role: 'user',
          content: [{ type: 'text', text: 'Say hello!' }],
          run: false,
        });

        await httpClient.post(`/api/conversations/${conversationId}/run`);

        // Wait for events to be received
        await waitFor(() => receivedEvents.length > 0, {
          timeout: 60000,
          interval: 500,
          message: 'No events received',
        });

        // Should have received at least one event
        expect(receivedEvents.length).toBeGreaterThan(0);

        // Events should have required properties
        for (const event of receivedEvents) {
          expect(event.id).toBeDefined();
          expect(event.kind).toBeDefined();
        }

        // Stop the WebSocket client
        wsClient.stop();
      },
      config?.testTimeout || 120000
    );

    it(
      'should receive different event types',
      async () => {
        if (SKIP_TESTS) return;

        // Create a conversation
        const createResponse = await httpClient.post<CreateConversationResponse>(
          '/api/conversations',
          {
            agent: {
              kind: 'Agent',
              llm: createTestLLMConfig(),
            },
            max_iterations: 15,
            stuck_detection: true,
            workspace: {
              type: 'local',
              working_dir: config.agentWorkspaceDir,
            },
          }
        );

        const conversationId = createResponse.data.id;
        const eventTypes = new Set<string>();
        const receivedEvents: Event[] = [];

        const wsClient = new WebSocketCallbackClient({
          host: config.agentServerUrl,
          conversationId,
          callback: (event) => {
            eventTypes.add(event.kind);
            receivedEvents.push(event);
          },
        });

        wsClient.start();
        await sleep(1000);

        // Send a message that requires the agent to do something
        await httpClient.post(`/api/conversations/${conversationId}/events`, {
          role: 'user',
          content: [{ type: 'text', text: 'List the files in the current directory.' }],
          run: false,
        });

        await httpClient.post(`/api/conversations/${conversationId}/run`);

        // Wait for multiple event types
        await waitFor(() => eventTypes.size > 1, {
          timeout: 90000,
          interval: 500,
          message: 'Not enough event types received',
        });

        // Should have received multiple event types
        expect(eventTypes.size).toBeGreaterThan(1);

        // Log received event types for debugging
        console.log('Received event types:', Array.from(eventTypes));

        wsClient.stop();
      },
      config?.testTimeout || 120000
    );

    it(
      'should handle WebSocket reconnection',
      async () => {
        if (SKIP_TESTS) return;

        // Create a conversation
        const createResponse = await httpClient.post<CreateConversationResponse>(
          '/api/conversations',
          {
            agent: {
              kind: 'Agent',
              llm: createTestLLMConfig(),
            },
            max_iterations: 5,
            stuck_detection: true,
            workspace: {
              type: 'local',
              working_dir: config.agentWorkspaceDir,
            },
          }
        );

        const conversationId = createResponse.data.id;
        let connectionCount = 0;
        const receivedEvents: Event[] = [];

        const wsClient = new WebSocketCallbackClient({
          host: config.agentServerUrl,
          conversationId,
          callback: (event) => {
            receivedEvents.push(event);
          },
        });

        // Start connection
        wsClient.start();
        await sleep(500);

        // Stop and restart to simulate reconnection
        wsClient.stop();
        await sleep(200);

        wsClient.start();
        await sleep(500);

        // Send a message
        await httpClient.post(`/api/conversations/${conversationId}/events`, {
          role: 'user',
          content: [{ type: 'text', text: 'Say "reconnected"' }],
          run: false,
        });

        await httpClient.post(`/api/conversations/${conversationId}/run`);

        // Wait for events
        await waitFor(() => receivedEvents.length > 0, {
          timeout: 60000,
          interval: 500,
          message: 'No events after reconnection',
        });

        expect(receivedEvents.length).toBeGreaterThan(0);

        wsClient.stop();
      },
      config?.testTimeout || 120000
    );

    it(
      'should stop cleanly without receiving more events',
      async () => {
        if (SKIP_TESTS) return;

        // Create a conversation
        const createResponse = await httpClient.post<CreateConversationResponse>(
          '/api/conversations',
          {
            agent: {
              kind: 'Agent',
              llm: createTestLLMConfig(),
            },
            max_iterations: 5,
            stuck_detection: true,
            workspace: {
              type: 'local',
              working_dir: config.agentWorkspaceDir,
            },
          }
        );

        const conversationId = createResponse.data.id;
        const receivedEvents: Event[] = [];

        const wsClient = new WebSocketCallbackClient({
          host: config.agentServerUrl,
          conversationId,
          callback: (event) => {
            receivedEvents.push(event);
          },
        });

        wsClient.start();
        await sleep(500);

        // Stop immediately
        wsClient.stop();

        const countAfterStop = receivedEvents.length;

        // Send a message (should not be received)
        await httpClient.post(`/api/conversations/${conversationId}/events`, {
          role: 'user',
          content: [{ type: 'text', text: 'This should not be received' }],
          run: false,
        });

        await httpClient.post(`/api/conversations/${conversationId}/run`);

        // Wait a bit
        await sleep(3000);

        // Should not have received new events after stop
        expect(receivedEvents.length).toBe(countAfterStop);
      },
      config?.testTimeout || 60000
    );
  });

  describe('Event Content', () => {
    // These tests are flaky because they depend on LLM behavior
    const testFn = SKIP_FLAKY_TESTS ? it.skip : it;

    testFn(
      'should receive MessageEvent with content',
      async () => {
        if (SKIP_TESTS) return;

        // Create a conversation
        const createResponse = await httpClient.post<CreateConversationResponse>(
          '/api/conversations',
          {
            agent: {
              kind: 'Agent',
              llm: createTestLLMConfig(),
            },
            max_iterations: 10,
            stuck_detection: true,
            workspace: {
              type: 'local',
              working_dir: config.agentWorkspaceDir,
            },
          }
        );

        const conversationId = createResponse.data.id;
        const messageEvents: Event[] = [];

        const wsClient = new WebSocketCallbackClient({
          host: config.agentServerUrl,
          conversationId,
          callback: (event) => {
            if (event.kind === 'MessageEvent') {
              messageEvents.push(event);
            }
          },
        });

        wsClient.start();
        await sleep(1000);

        await httpClient.post(`/api/conversations/${conversationId}/events`, {
          role: 'user',
          content: [{ type: 'text', text: 'What is 5 + 3?' }],
          run: false,
        });

        await httpClient.post(`/api/conversations/${conversationId}/run`);

        // Wait for message events
        await waitFor(() => messageEvents.length > 0, {
          timeout: 60000,
          interval: 500,
          message: 'No MessageEvents received',
        });

        expect(messageEvents.length).toBeGreaterThan(0);

        // MessageEvents should have llm_message
        const firstMessage = messageEvents[0];
        expect(firstMessage).toHaveProperty('llm_message');

        wsClient.stop();
      },
      config?.testTimeout || 120000
    );

    testFn(
      'should receive ActionEvent when agent performs action',
      async () => {
        if (SKIP_TESTS) return;

        // Create a conversation
        const createResponse = await httpClient.post<CreateConversationResponse>(
          '/api/conversations',
          {
            agent: {
              kind: 'Agent',
              llm: createTestLLMConfig(),
            },
            max_iterations: 15,
            stuck_detection: true,
            workspace: {
              type: 'local',
              working_dir: config.agentWorkspaceDir,
            },
          }
        );

        const conversationId = createResponse.data.id;
        const actionEvents: Event[] = [];

        const wsClient = new WebSocketCallbackClient({
          host: config.agentServerUrl,
          conversationId,
          callback: (event) => {
            if (event.kind === 'ActionEvent') {
              actionEvents.push(event);
            }
          },
        });

        wsClient.start();
        await sleep(1000);

        // Ask agent to perform an action - use a simple task that reliably triggers actions
        await httpClient.post(`/api/conversations/${conversationId}/events`, {
          role: 'user',
          content: [{ type: 'text', text: 'List the files in the current directory.' }],
          run: false,
        });

        await httpClient.post(`/api/conversations/${conversationId}/run`);

        // Wait for action events with longer timeout for LLM-dependent tests
        await waitFor(() => actionEvents.length > 0, {
          timeout: 120000,
          interval: 500,
          message: 'No ActionEvents received',
        });

        expect(actionEvents.length).toBeGreaterThan(0);

        // ActionEvents should have action property
        const firstAction = actionEvents[0];
        expect(firstAction).toHaveProperty('action');

        wsClient.stop();
      },
      config?.testTimeout || 180000
    );

    testFn(
      'should receive ObservationEvent after action',
      async () => {
        if (SKIP_TESTS) return;

        // Create a conversation
        const createResponse = await httpClient.post<CreateConversationResponse>(
          '/api/conversations',
          {
            agent: {
              kind: 'Agent',
              llm: createTestLLMConfig(),
            },
            max_iterations: 15,
            stuck_detection: true,
            workspace: {
              type: 'local',
              working_dir: config.agentWorkspaceDir,
            },
          }
        );

        const conversationId = createResponse.data.id;
        const observationEvents: Event[] = [];

        const wsClient = new WebSocketCallbackClient({
          host: config.agentServerUrl,
          conversationId,
          callback: (event) => {
            if (event.kind === 'ObservationEvent') {
              observationEvents.push(event);
            }
          },
        });

        wsClient.start();
        await sleep(1000);

        // Ask agent to run a command (which produces observation) - use a simple task
        await httpClient.post(`/api/conversations/${conversationId}/events`, {
          role: 'user',
          content: [{ type: 'text', text: 'List the files in the current directory.' }],
          run: false,
        });

        await httpClient.post(`/api/conversations/${conversationId}/run`);

        // Wait for observation events with longer timeout for LLM-dependent tests
        await waitFor(() => observationEvents.length > 0, {
          timeout: 120000,
          interval: 500,
          message: 'No ObservationEvents received',
        });

        expect(observationEvents.length).toBeGreaterThan(0);

        // ObservationEvents should have observation property
        const firstObs = observationEvents[0];
        expect(firstObs).toHaveProperty('observation');

        wsClient.stop();
      },
      config?.testTimeout || 180000
    );
  });

  describe('ConversationStateUpdateEvent', () => {
    it(
      'should receive state update events',
      async () => {
        if (SKIP_TESTS) return;

        // Create a conversation
        const createResponse = await httpClient.post<CreateConversationResponse>(
          '/api/conversations',
          {
            agent: {
              kind: 'Agent',
              llm: createTestLLMConfig(),
            },
            max_iterations: 10,
            stuck_detection: true,
            workspace: {
              type: 'local',
              working_dir: config.agentWorkspaceDir,
            },
          }
        );

        const conversationId = createResponse.data.id;
        const stateUpdateEvents: Event[] = [];

        const wsClient = new WebSocketCallbackClient({
          host: config.agentServerUrl,
          conversationId,
          callback: (event) => {
            if (event.kind === 'ConversationStateUpdateEvent') {
              stateUpdateEvents.push(event);
            }
          },
        });

        wsClient.start();
        await sleep(1000);

        await httpClient.post(`/api/conversations/${conversationId}/events`, {
          role: 'user',
          content: [{ type: 'text', text: 'Say OK' }],
          run: false,
        });

        await httpClient.post(`/api/conversations/${conversationId}/run`);

        // Wait for state update events
        await waitFor(() => stateUpdateEvents.length > 0, {
          timeout: 60000,
          interval: 500,
          message: 'No ConversationStateUpdateEvents received',
        });

        expect(stateUpdateEvents.length).toBeGreaterThan(0);

        // State update events should have key and value
        const firstUpdate = stateUpdateEvents[0];
        expect(firstUpdate).toHaveProperty('key');
        expect(firstUpdate).toHaveProperty('value');

        wsClient.stop();
      },
      config?.testTimeout || 120000
    );
  });
});
