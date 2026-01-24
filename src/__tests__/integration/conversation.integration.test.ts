/**
 * Integration tests for RemoteConversation and Conversation
 *
 * Tests conversation lifecycle including creation, sending messages,
 * running the agent, and receiving events against a real agent-server.
 *
 * NOTE: Some tests in this file are skipped by default because they depend on
 * LLM behavior which is non-deterministic and can be slow.
 */

import {
  Conversation,
  RemoteConversation,
  Workspace,
  Agent,
  Event,
  AgentExecutionStatus,
} from '../../index';
import { getTestConfig, skipIfNoConfig, createTestLLMConfig } from './test-config';
import {
  waitFor,
  waitForAgentIdle,
  sleep,
  workspaceFileExists,
  readWorkspaceFile,
  deleteWorkspaceFile,
  uniqueFileName,
} from './test-utils';

const SKIP_TESTS = skipIfNoConfig();
// Skip flaky tests that depend on LLM behavior in CI
const SKIP_FLAKY_TESTS = process.env.SKIP_FLAKY_TESTS !== 'false';

describe('Conversation Integration Tests', () => {
  let config: ReturnType<typeof getTestConfig>;

  beforeAll(() => {
    if (SKIP_TESTS) {
      console.warn('Skipping integration tests: LLM_API_KEY and LLM_MODEL not set');
      return;
    }
    config = getTestConfig();
  });

  describe('Conversation Creation', () => {
    it(
      'should create a new conversation',
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

        // Start the conversation
        await conversation.start();

        expect(conversation).toBeInstanceOf(RemoteConversation);
        expect(conversation.id).toBeDefined();
        expect(typeof conversation.id).toBe('string');
        expect(conversation.id.length).toBeGreaterThan(0);

        // Cleanup
        await conversation.close();
      },
      config?.testTimeout || 60000
    );

    it(
      'should create conversation with initial message',
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

        // Start with initial message
        await conversation.start({
          initialMessage: 'Hello, this is an initial message.',
          maxIterations: 5,
        });

        expect(conversation.id).toBeDefined();

        // Cleanup
        await conversation.close();
      },
      config?.testTimeout || 60000
    );
  });

  describe('Conversation State', () => {
    it(
      'should access conversation state after creation',
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

        // Access state
        const state = conversation.state;
        expect(state).toBeDefined();
        expect(state.id).toBe(conversation.id);

        // Get agent status
        const status = await state.getAgentStatus();
        expect(Object.values(AgentExecutionStatus)).toContain(status);

        // Cleanup
        await conversation.close();
      },
      config?.testTimeout || 60000
    );

    it(
      'should get conversation stats',
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

        const stats = await conversation.conversationStats();
        expect(stats).toBeDefined();

        // Cleanup
        await conversation.close();
      },
      config?.testTimeout || 60000
    );
  });

  describe('Sending Messages', () => {
    it(
      'should send a string message',
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

        // Send message (should not throw)
        await conversation.sendMessage('What is 2 + 2?');

        // Cleanup
        await conversation.close();
      },
      config?.testTimeout || 60000
    );

    it(
      'should send a Message object',
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

        // Send message as Message object
        await conversation.sendMessage({
          role: 'user',
          content: [{ type: 'text', text: 'Hello from Message object' }],
        });

        // Cleanup
        await conversation.close();
      },
      config?.testTimeout || 60000
    );
  });

  describe('Running Agent', () => {
    // These tests are flaky because they depend on LLM behavior
    const testFn = SKIP_FLAKY_TESTS ? it.skip : it;

    testFn(
      'should run the agent after sending a message',
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

        // Send a simple message
        await conversation.sendMessage('Reply with only the word "ACKNOWLEDGED".');

        // Run the agent
        await conversation.run();

        // Wait for agent to finish (poll status)
        await waitForAgentIdle(async () => {
          return await conversation.state.getAgentStatus();
        });

        const finalStatus = await conversation.state.getAgentStatus();
        expect([AgentExecutionStatus.IDLE, AgentExecutionStatus.FINISHED]).toContain(finalStatus);

        // Cleanup
        await conversation.close();
      },
      config?.testTimeout || 120000
    );

    it(
      'should pause the agent',
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
        await conversation.start({ maxIterations: 50 });

        // Send a message that might take a while
        await conversation.sendMessage('Count from 1 to 100, saying each number.');

        // Run the agent
        await conversation.run();

        // Wait a bit then pause
        await sleep(1000);

        // Pause (should not throw, even if agent is idle)
        await conversation.pause();

        // Cleanup
        await conversation.close();
      },
      config?.testTimeout || 60000
    );
  });

  describe('Event Handling with Callbacks', () => {
    it(
      'should receive events via callback',
      async () => {
        if (SKIP_TESTS) return;

        const agent = new Agent({
          llm: createTestLLMConfig(),
        });

        const workspace = new Workspace({
          host: config.agentServerUrl,
          workingDir: config.agentWorkspaceDir,
        });

        const receivedEvents: Event[] = [];
        const callback = (event: Event) => {
          receivedEvents.push(event);
        };

        const conversation = new Conversation(agent, workspace, { callback });
        await conversation.start({ maxIterations: 10 });

        // Start WebSocket client for events
        await conversation.startWebSocketClient();

        // Give a moment for connection to establish
        await sleep(500);

        // Send a message and run
        await conversation.sendMessage('Say "Hello Test!" exactly.');
        await conversation.run();

        // Wait for agent to finish
        await waitForAgentIdle(async () => {
          return await conversation.state.getAgentStatus();
        });

        // Wait for events to be received
        await sleep(2000);

        // Should have received some events
        expect(receivedEvents.length).toBeGreaterThan(0);

        // Cleanup
        await conversation.stopWebSocketClient();
        await conversation.close();
      },
      config?.testTimeout || 120000
    );

    it(
      'should track different event types',
      async () => {
        if (SKIP_TESTS) return;

        const agent = new Agent({
          llm: createTestLLMConfig(),
        });

        const workspace = new Workspace({
          host: config.agentServerUrl,
          workingDir: config.agentWorkspaceDir,
        });

        const eventTypes = new Set<string>();
        const callback = (event: Event) => {
          eventTypes.add(event.kind);
        };

        const conversation = new Conversation(agent, workspace, { callback });
        await conversation.start({ maxIterations: 10 });

        await conversation.startWebSocketClient();
        await sleep(500);

        await conversation.sendMessage('What time is it?');
        await conversation.run();

        await waitForAgentIdle(async () => {
          return await conversation.state.getAgentStatus();
        });

        await sleep(2000);

        // Should have received multiple event types
        expect(eventTypes.size).toBeGreaterThan(0);

        // Cleanup
        await conversation.stopWebSocketClient();
        await conversation.close();
      },
      config?.testTimeout || 120000
    );
  });

  describe('Events List', () => {
    // These tests are flaky because they depend on LLM behavior
    const testFn = SKIP_FLAKY_TESTS ? it.skip : it;

    testFn(
      'should fetch events from conversation state',
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

        await conversation.sendMessage('Just say "OK".');
        await conversation.run();

        await waitForAgentIdle(async () => {
          return await conversation.state.getAgentStatus();
        });

        // Fetch events from state
        const events = await conversation.state.events.getEvents();
        expect(events.length).toBeGreaterThan(0);

        // Events should have required properties
        for (const event of events) {
          expect(event.id).toBeDefined();
          expect(event.kind).toBeDefined();
          expect(event.timestamp).toBeDefined();
        }

        // Cleanup
        await conversation.close();
      },
      config?.testTimeout || 120000
    );
  });

  describe('Title Generation', () => {
    // These tests are flaky because they depend on LLM behavior
    const testFn = SKIP_FLAKY_TESTS ? it.skip : it;

    testFn(
      'should generate a conversation title',
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

        // Send a message about a specific topic
        await conversation.sendMessage('Tell me about TypeScript testing best practices.');
        await conversation.run();

        await waitForAgentIdle(async () => {
          return await conversation.state.getAgentStatus();
        });

        // Generate title
        const title = await conversation.generateTitle(50);
        expect(title).toBeDefined();
        expect(typeof title).toBe('string');
        expect(title.length).toBeGreaterThan(0);
        expect(title.length).toBeLessThanOrEqual(50);

        // Cleanup
        await conversation.close();
      },
      config?.testTimeout || 120000
    );
  });

  describe('Secrets', () => {
    it(
      'should update secrets',
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

        // Update secrets (should not throw)
        await conversation.updateSecrets({
          TEST_SECRET: 'test-value',
          ANOTHER_SECRET: () => 'dynamic-value',
        });

        // Cleanup
        await conversation.close();
      },
      config?.testTimeout || 60000
    );
  });

  describe('Agent Task Execution', () => {
    // Skip flaky tests that depend on LLM behavior in CI
    const SKIP_FLAKY_TESTS = process.env.SKIP_FLAKY_TESTS !== 'false';
    const testFn = SKIP_FLAKY_TESTS ? it.skip : it;

    testFn(
      'should execute task that creates a file',
      async () => {
        if (SKIP_TESTS) return;

        const fileName = uniqueFileName('agent-created');
        const fullPath = `${config.agentWorkspaceDir}/${fileName}`;
        const expectedContent = 'Hello from the agent!';

        const agent = new Agent({
          llm: createTestLLMConfig(),
        });

        const workspace = new Workspace({
          host: config.agentServerUrl,
          workingDir: config.agentWorkspaceDir,
        });

        const conversation = new Conversation(agent, workspace);
        await conversation.start({ maxIterations: 20 });

        // Ask the agent to create a file
        await conversation.sendMessage(
          `Create a file at "${fullPath}" with exactly this content: "${expectedContent}". Use the terminal tool to create the file with echo.`
        );
        await conversation.run();

        // Wait for agent to complete
        await waitForAgentIdle(
          async () => {
            return await conversation.state.getAgentStatus();
          },
          { timeout: 90000 }
        );

        // Wait for file system to sync
        await sleep(1000);

        // Check if file was created in the mounted workspace
        const fileExists = workspaceFileExists(fileName);
        expect(fileExists).toBe(true);

        if (fileExists) {
          const content = readWorkspaceFile(fileName);
          expect(content.trim()).toContain(expectedContent);
          deleteWorkspaceFile(fileName);
        }

        // Cleanup
        await conversation.close();
      },
      config?.testTimeout || 180000
    );

    it(
      'should execute task that reads an existing file',
      async () => {
        if (SKIP_TESTS) return;

        const fileName = uniqueFileName('to-read');
        const fullPath = `${config.agentWorkspaceDir}/${fileName}`;
        const fileContent = 'This file contains the secret number: 42';

        // Create the file first
        const agent = new Agent({
          llm: createTestLLMConfig(),
        });

        const workspace = new Workspace({
          host: config.agentServerUrl,
          workingDir: config.agentWorkspaceDir,
        });

        // Create file via workspace
        await workspace.uploadText(fileContent, fullPath, fileName);
        await sleep(500);

        const receivedEvents: Event[] = [];
        const callback = (event: Event) => {
          receivedEvents.push(event);
        };

        const conversation = new Conversation(agent, workspace, { callback });
        await conversation.start({ maxIterations: 20 });
        await conversation.startWebSocketClient();
        await sleep(500);

        // Ask the agent to read the file and tell us what's in it
        await conversation.sendMessage(
          `Read the file at "${fullPath}" and tell me what secret number is mentioned in it.`
        );
        await conversation.run();

        // Wait for agent to complete
        await waitForAgentIdle(
          async () => {
            return await conversation.state.getAgentStatus();
          },
          { timeout: 90000 }
        );

        // Wait for events
        await sleep(2000);

        // Check that agent received events (indicating activity)
        expect(receivedEvents.length).toBeGreaterThan(0);

        // Look for message events that might contain the answer
        const messageEvents = receivedEvents.filter((e) => e.kind === 'MessageEvent');
        expect(messageEvents.length).toBeGreaterThan(0);

        // Cleanup
        deleteWorkspaceFile(fileName);
        await conversation.stopWebSocketClient();
        await conversation.close();
      },
      config?.testTimeout || 180000
    );
  });
});
