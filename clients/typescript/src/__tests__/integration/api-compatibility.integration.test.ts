/**
 * Integration tests for API compatibility with agent-server
 *
 * These tests verify that the TypeScript client correctly handles
 * breaking changes between different versions of the agent-server API.
 *
 * Key changes tested:
 * - AgentExecutionStatus -> ConversationExecutionStatus rename
 * - agent_status -> execution_status field rename
 * - New endpoints: ask_agent, condense, security_analyzer
 * - New event search filters: source, body, timestamp__gte, timestamp__lt
 *
 * NOTE: Some tests in this file are skipped by default because they depend on
 * LLM behavior which is non-deterministic and can be slow.
 */

import {
  Conversation,
  Workspace,
  Agent,
  ConversationExecutionStatus,
  AgentExecutionStatus,
} from '../../index';
import { getTestConfig, skipIfNoConfig, createTestLLMConfig } from './test-config';
import { waitForAgentIdle, sleep } from './test-utils';

const SKIP_TESTS = skipIfNoConfig();
// Skip flaky tests that depend on LLM behavior in CI
const SKIP_FLAKY_TESTS = process.env.SKIP_FLAKY_TESTS !== 'false';

describe('API Compatibility Integration Tests', () => {
  let config: ReturnType<typeof getTestConfig>;

  beforeAll(() => {
    if (SKIP_TESTS) {
      console.warn('Skipping integration tests: LLM_API_KEY and LLM_MODEL not set');
      return;
    }
    config = getTestConfig();
  });

  describe('ConversationExecutionStatus Enum', () => {
    it('should have all expected status values', () => {
      // Verify the enum has all expected values
      expect(ConversationExecutionStatus.IDLE).toBe('idle');
      expect(ConversationExecutionStatus.RUNNING).toBe('running');
      expect(ConversationExecutionStatus.PAUSED).toBe('paused');
      expect(ConversationExecutionStatus.WAITING_FOR_CONFIRMATION).toBe('waiting_for_confirmation');
      expect(ConversationExecutionStatus.FINISHED).toBe('finished');
      expect(ConversationExecutionStatus.ERROR).toBe('error');
      expect(ConversationExecutionStatus.STUCK).toBe('stuck');
      expect(ConversationExecutionStatus.DELETING).toBe('deleting');
    });

    it('should have AgentExecutionStatus as an alias', () => {
      // Verify backward compatibility alias
      expect(AgentExecutionStatus).toBe(ConversationExecutionStatus);
      expect(AgentExecutionStatus.IDLE).toBe('idle');
    });
  });

  describe('Execution Status Field', () => {
    it(
      'should get execution status using getExecutionStatus()',
      async () => {
        if (SKIP_TESTS) return;

        const agent = new Agent({
          llm: createTestLLMConfig(),
        });

        const workspace = new Workspace({
          host: config.agentServerUrl,
          workingDir: config.agentWorkspaceDir,
        });

        const conversation = new Conversation(agent, workspace);
        await conversation.start();

        // Use the new method
        const status = await conversation.state.getExecutionStatus();
        expect(Object.values(ConversationExecutionStatus)).toContain(status);

        // Cleanup
        await conversation.close();
      },
      config?.testTimeout || 60000
    );

    it(
      'should get execution status using deprecated getAgentStatus()',
      async () => {
        if (SKIP_TESTS) return;

        const agent = new Agent({
          llm: createTestLLMConfig(),
        });

        const workspace = new Workspace({
          host: config.agentServerUrl,
          workingDir: config.agentWorkspaceDir,
        });

        const conversation = new Conversation(agent, workspace);
        await conversation.start();

        // Use the deprecated method (should still work)
        const status = await conversation.state.getAgentStatus();
        expect(Object.values(ConversationExecutionStatus)).toContain(status);

        // Cleanup
        await conversation.close();
      },
      config?.testTimeout || 60000
    );
  });

  describe('New API Endpoints', () => {
    // Skip flaky tests that depend on LLM behavior in CI
    const SKIP_FLAKY_TESTS = process.env.SKIP_FLAKY_TESTS !== 'false';
    const testFn = SKIP_FLAKY_TESTS ? it.skip : it;

    testFn(
      'should call askAgent endpoint',
      async () => {
        if (SKIP_TESTS) return;

        const agent = new Agent({
          llm: createTestLLMConfig(),
        });

        const workspace = new Workspace({
          host: config.agentServerUrl,
          workingDir: config.agentWorkspaceDir,
        });

        const conversation = new Conversation(agent, workspace);
        await conversation.start({ maxIterations: 10 });

        // Send a message first to establish context
        await conversation.sendMessage('Hello, I am testing the API.');
        await conversation.run();

        await waitForAgentIdle(async () => {
          return await conversation.state.getExecutionStatus();
        });

        // Ask a simple question
        const response = await conversation.askAgent('What is 2 + 2?');
        expect(response).toBeDefined();
        expect(typeof response).toBe('string');
        expect(response.length).toBeGreaterThan(0);

        // Cleanup
        await conversation.close();
      },
      config?.testTimeout || 120000
    );

    // Skip: condense endpoint may not be available in all agent-server versions
    it.skip(
      'should call condense endpoint',
      async () => {
        if (SKIP_TESTS) return;

        const agent = new Agent({
          llm: createTestLLMConfig(),
        });

        const workspace = new Workspace({
          host: config.agentServerUrl,
          workingDir: config.agentWorkspaceDir,
        });

        const conversation = new Conversation(agent, workspace);
        await conversation.start({ maxIterations: 10 });

        // Send a message to create some history
        await conversation.sendMessage('Hello, this is a test message.');
        await conversation.run();

        await waitForAgentIdle(async () => {
          return await conversation.state.getExecutionStatus();
        });

        // Call condense (should not throw)
        await conversation.condense();

        // Cleanup
        await conversation.close();
      },
      config?.testTimeout || 120000
    );

    // Skip: setSecurityAnalyzer endpoint may not be available in all agent-server versions
    it.skip(
      'should call setSecurityAnalyzer endpoint',
      async () => {
        if (SKIP_TESTS) return;

        const agent = new Agent({
          llm: createTestLLMConfig(),
        });

        const workspace = new Workspace({
          host: config.agentServerUrl,
          workingDir: config.agentWorkspaceDir,
        });

        const conversation = new Conversation(agent, workspace);
        await conversation.start({ maxIterations: 5 });

        // Set security analyzer to null (should not throw)
        await conversation.setSecurityAnalyzer(null);

        // Cleanup
        await conversation.close();
      },
      config?.testTimeout || 60000
    );
  });

  describe('Event Search Filters', () => {
    // These tests are flaky because they depend on LLM behavior
    const testFn = SKIP_FLAKY_TESTS ? it.skip : it;

    testFn(
      'should search events with kind filter',
      async () => {
        if (SKIP_TESTS) return;

        const agent = new Agent({
          llm: createTestLLMConfig(),
        });

        const workspace = new Workspace({
          host: config.agentServerUrl,
          workingDir: config.agentWorkspaceDir,
        });

        const conversation = new Conversation(agent, workspace);
        await conversation.start({ maxIterations: 10 });

        // Send a message and run
        await conversation.sendMessage('Say hello.');
        await conversation.run();

        await waitForAgentIdle(async () => {
          return await conversation.state.getExecutionStatus();
        });

        // Search for MessageEvent kind
        const result = await conversation.state.events.search({
          kind: 'MessageEvent',
          limit: 10,
        });

        expect(result).toBeDefined();
        expect(result.items).toBeDefined();
        expect(Array.isArray(result.items)).toBe(true);

        // All returned events should be MessageEvent
        for (const event of result.items) {
          expect(event.kind).toBe('MessageEvent');
        }

        // Cleanup
        await conversation.close();
      },
      config?.testTimeout || 120000
    );

    // Skip: source filter may not be fully supported in all agent-server versions
    // The server may return events from other sources even when filtering by source
    it.skip(
      'should search events with source filter',
      async () => {
        if (SKIP_TESTS) return;

        const agent = new Agent({
          llm: createTestLLMConfig(),
        });

        const workspace = new Workspace({
          host: config.agentServerUrl,
          workingDir: config.agentWorkspaceDir,
        });

        const conversation = new Conversation(agent, workspace);
        await conversation.start({ maxIterations: 10 });

        // Send a message and run
        await conversation.sendMessage('Hello from user.');
        await conversation.run();

        await waitForAgentIdle(async () => {
          return await conversation.state.getExecutionStatus();
        });

        // Search for user events
        const result = await conversation.state.events.search({
          source: 'user',
          limit: 10,
        });

        expect(result).toBeDefined();
        expect(result.items).toBeDefined();
        expect(Array.isArray(result.items)).toBe(true);

        // All returned events should have source 'user'
        for (const event of result.items) {
          expect(event.source).toBe('user');
        }

        // Cleanup
        await conversation.close();
      },
      config?.testTimeout || 120000
    );

    testFn(
      'should count events with filters',
      async () => {
        if (SKIP_TESTS) return;

        const agent = new Agent({
          llm: createTestLLMConfig(),
        });

        const workspace = new Workspace({
          host: config.agentServerUrl,
          workingDir: config.agentWorkspaceDir,
        });

        const conversation = new Conversation(agent, workspace);
        await conversation.start({ maxIterations: 10 });

        // Send a message and run
        await conversation.sendMessage('Test message for counting.');
        await conversation.run();

        await waitForAgentIdle(async () => {
          return await conversation.state.getExecutionStatus();
        });

        // Count all events
        const totalCount = await conversation.state.events.count();
        expect(totalCount).toBeGreaterThan(0);

        // Count MessageEvent events
        const messageCount = await conversation.state.events.count({
          kind: 'MessageEvent',
        });
        expect(messageCount).toBeGreaterThanOrEqual(0);
        expect(messageCount).toBeLessThanOrEqual(totalCount);

        // Cleanup
        await conversation.close();
      },
      config?.testTimeout || 120000
    );

    testFn(
      'should search events with timestamp filters',
      async () => {
        if (SKIP_TESTS) return;

        const agent = new Agent({
          llm: createTestLLMConfig(),
        });

        const workspace = new Workspace({
          host: config.agentServerUrl,
          workingDir: config.agentWorkspaceDir,
        });

        const conversation = new Conversation(agent, workspace);
        await conversation.start({ maxIterations: 10 });

        // Record start time
        const startTime = new Date().toISOString();

        // Send a message and run
        await conversation.sendMessage('Test message with timestamp.');
        await conversation.run();

        await waitForAgentIdle(async () => {
          return await conversation.state.getExecutionStatus();
        });

        // Wait a bit
        await sleep(100);

        // Record end time
        const endTime = new Date().toISOString();

        // Search for events within the time range
        const result = await conversation.state.events.search({
          timestamp__gte: startTime,
          timestamp__lt: endTime,
          limit: 100,
        });

        expect(result).toBeDefined();
        expect(result.items).toBeDefined();
        expect(Array.isArray(result.items)).toBe(true);

        // Cleanup
        await conversation.close();
      },
      config?.testTimeout || 120000
    );

    testFn(
      'should search events with sort order',
      async () => {
        if (SKIP_TESTS) return;

        const agent = new Agent({
          llm: createTestLLMConfig(),
        });

        const workspace = new Workspace({
          host: config.agentServerUrl,
          workingDir: config.agentWorkspaceDir,
        });

        const conversation = new Conversation(agent, workspace);
        await conversation.start({ maxIterations: 10 });

        // Send a message and run
        await conversation.sendMessage('Test message for sorting.');
        await conversation.run();

        await waitForAgentIdle(async () => {
          return await conversation.state.getExecutionStatus();
        });

        // Search with ascending order
        const ascResult = await conversation.state.events.search({
          sort_order: 'TIMESTAMP',
          limit: 10,
        });

        // Search with descending order
        const descResult = await conversation.state.events.search({
          sort_order: 'TIMESTAMP_DESC',
          limit: 10,
        });

        expect(ascResult.items).toBeDefined();
        expect(descResult.items).toBeDefined();

        // If we have multiple events, verify the order is different
        if (ascResult.items.length > 1 && descResult.items.length > 1) {
          // First event in ascending should be oldest
          // First event in descending should be newest
          expect(ascResult.items[0].id).not.toBe(descResult.items[0].id);
        }

        // Cleanup
        await conversation.close();
      },
      config?.testTimeout || 120000
    );
  });
});
