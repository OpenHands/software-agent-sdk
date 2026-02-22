/**
 * Tests for rich event types
 *
 * These tests verify the event type definitions and type guards.
 */

import {
  MessageEvent,
  ActionEvent,
  ObservationEvent,
  AgentErrorEvent,
  SystemPromptEvent,
  PauseEvent,
  CondensationRequestEvent,
  CondensationSummaryEvent,
  ConversationStateUpdateEvent,
  UserRejectObservation,
  TokenEvent,
  isMessageEvent,
  isActionEvent,
  isObservationEvent,
  isAgentErrorEvent,
  isObservationLike,
  generateEventId,
} from '../events/types';

describe('Event Type Guards', () => {
  describe('isMessageEvent', () => {
    it('should return true for MessageEvent', () => {
      const event: MessageEvent = {
        id: generateEventId(),
        kind: 'MessageEvent',
        timestamp: new Date().toISOString(),
        source: 'user',
        llm_message: {
          role: 'user',
          content: [{ type: 'text', text: 'Hello' }],
        },
      };

      expect(isMessageEvent(event)).toBe(true);
    });

    it('should return false for non-MessageEvent', () => {
      const event: ActionEvent = {
        id: generateEventId(),
        kind: 'ActionEvent',
        timestamp: new Date().toISOString(),
        source: 'agent',
        tool_name: 'test',
        tool_call_id: 'call_1',
        action: {},
      };

      expect(isMessageEvent(event)).toBe(false);
    });
  });

  describe('isActionEvent', () => {
    it('should return true for ActionEvent', () => {
      const event: ActionEvent = {
        id: generateEventId(),
        kind: 'ActionEvent',
        timestamp: new Date().toISOString(),
        source: 'agent',
        tool_name: 'terminal',
        tool_call_id: 'call_1',
        action: { command: 'ls' },
        thought: 'Running ls command',
      };

      expect(isActionEvent(event)).toBe(true);
    });

    it('should return false for non-ActionEvent', () => {
      const event: ObservationEvent = {
        id: generateEventId(),
        kind: 'ObservationEvent',
        timestamp: new Date().toISOString(),
        source: 'environment',
        tool_name: 'terminal',
        tool_call_id: 'call_1',
        observation: { output: 'file.txt' },
        action_id: 'action_1',
      };

      expect(isActionEvent(event)).toBe(false);
    });
  });

  describe('isObservationEvent', () => {
    it('should return true for ObservationEvent', () => {
      const event: ObservationEvent = {
        id: generateEventId(),
        kind: 'ObservationEvent',
        timestamp: new Date().toISOString(),
        source: 'environment',
        tool_name: 'terminal',
        tool_call_id: 'call_1',
        observation: { stdout: 'output', exit_code: 0 },
        action_id: 'action_1',
      };

      expect(isObservationEvent(event)).toBe(true);
    });
  });

  describe('isAgentErrorEvent', () => {
    it('should return true for AgentErrorEvent', () => {
      const event: AgentErrorEvent = {
        id: generateEventId(),
        kind: 'AgentErrorEvent',
        timestamp: new Date().toISOString(),
        source: 'agent',
        tool_name: 'terminal',
        tool_call_id: 'call_1',
        error: 'Command failed',
      };

      expect(isAgentErrorEvent(event)).toBe(true);
    });
  });

  describe('SystemPromptEvent', () => {
    it('should have correct structure', () => {
      const event: SystemPromptEvent = {
        id: generateEventId(),
        kind: 'SystemPromptEvent',
        timestamp: new Date().toISOString(),
        source: 'environment',
        system_prompt: { type: 'text', text: 'You are a helpful assistant.' },
        tools: [],
      };

      expect(event.kind).toBe('SystemPromptEvent');
      expect(event.system_prompt.type).toBe('text');
    });
  });

  describe('PauseEvent', () => {
    it('should have correct structure', () => {
      const event: PauseEvent = {
        id: generateEventId(),
        kind: 'PauseEvent',
        timestamp: new Date().toISOString(),
        source: 'user',
      };

      expect(event.kind).toBe('PauseEvent');
    });
  });

  describe('CondensationEvents', () => {
    it('should have correct structure for CondensationRequestEvent', () => {
      const event: CondensationRequestEvent = {
        id: generateEventId(),
        kind: 'CondensationRequest',
        timestamp: new Date().toISOString(),
        source: 'agent',
      };

      expect(event.kind).toBe('CondensationRequest');
    });

    it('should have correct structure for CondensationSummaryEvent', () => {
      const event: CondensationSummaryEvent = {
        id: generateEventId(),
        kind: 'CondensationSummaryEvent',
        timestamp: new Date().toISOString(),
        source: 'agent',
        summary: 'Conversation summary here',
      };

      expect(event.kind).toBe('CondensationSummaryEvent');
      expect(event.summary).toBe('Conversation summary here');
    });
  });

  describe('ConversationStateUpdateEvent', () => {
    it('should have correct structure', () => {
      const event: ConversationStateUpdateEvent = {
        id: generateEventId(),
        kind: 'ConversationStateUpdateEvent',
        timestamp: new Date().toISOString(),
        source: 'environment',
        key: 'execution_status',
        value: 'running',
      };

      expect(event.kind).toBe('ConversationStateUpdateEvent');
      expect(event.key).toBe('execution_status');
    });
  });

  describe('isObservationLike', () => {
    it('should return true for observation-like events', () => {
      const obs: ObservationEvent = {
        id: generateEventId(),
        kind: 'ObservationEvent',
        timestamp: new Date().toISOString(),
        source: 'environment',
        tool_name: 'terminal',
        tool_call_id: 'call_1',
        observation: { output: 'test' },
        action_id: 'action_1',
      };

      const error: AgentErrorEvent = {
        id: generateEventId(),
        kind: 'AgentErrorEvent',
        timestamp: new Date().toISOString(),
        source: 'agent',
        tool_name: 'terminal',
        tool_call_id: 'call_1',
        error: 'Failed',
      };

      const reject: UserRejectObservation = {
        id: generateEventId(),
        kind: 'UserRejectObservation',
        timestamp: new Date().toISOString(),
        source: 'user',
        action_id: 'action_1',
        tool_name: 'execute_command',
        tool_call_id: 'tool_call_1',
        rejection_reason: 'Too risky',
        rejection_source: 'user',
      };

      expect(isObservationLike(obs)).toBe(true);
      expect(isObservationLike(error)).toBe(true);
      expect(isObservationLike(reject)).toBe(true);
    });
  });
});

describe('Event Structure', () => {
  describe('MessageEvent', () => {
    it('should contain llm_message field', () => {
      const event: MessageEvent = {
        id: generateEventId(),
        kind: 'MessageEvent',
        timestamp: new Date().toISOString(),
        source: 'user',
        llm_message: {
          role: 'user',
          content: [{ type: 'text', text: 'Hello, assistant!' }],
        },
        activated_skills: ['code_review'],
      };

      expect(event.llm_message).toBeDefined();
      expect(event.llm_message.role).toBe('user');
      expect(event.llm_message.content).toHaveLength(1);
      expect(event.activated_skills).toContain('code_review');
    });
  });

  describe('ActionEvent', () => {
    it('should contain action details', () => {
      const event: ActionEvent = {
        id: generateEventId(),
        kind: 'ActionEvent',
        timestamp: new Date().toISOString(),
        source: 'agent',
        tool_name: 'terminal',
        tool_call_id: 'call_123',
        action: {
          command: 'ls -la /workspace',
        },
        thought: 'I need to list the workspace files',
      };

      expect(event.tool_name).toBe('terminal');
      expect(event.action.command).toBe('ls -la /workspace');
      expect(event.thought).toBeDefined();
    });
  });

  describe('ObservationEvent', () => {
    it('should contain observation and action_id', () => {
      const event: ObservationEvent = {
        id: generateEventId(),
        kind: 'ObservationEvent',
        timestamp: new Date().toISOString(),
        source: 'environment',
        tool_name: 'terminal',
        tool_call_id: 'call_123',
        observation: {
          stdout: 'file1.txt\nfile2.txt',
          stderr: '',
          exit_code: 0,
        },
        action_id: 'action_456',
      };

      expect(event.action_id).toBe('action_456');
      // observation is typed as unknown, cast to check structure
      const obs = event.observation as { stdout: string; exit_code: number };
      expect(obs.stdout).toBeDefined();
      expect(obs.exit_code).toBe(0);
    });
  });

  describe('TokenEvent', () => {
    it('should contain token IDs information', () => {
      const event: TokenEvent = {
        id: generateEventId(),
        kind: 'TokenEvent',
        timestamp: new Date().toISOString(),
        source: 'agent',
        prompt_token_ids: [1, 2, 3],
        response_token_ids: [4, 5, 6],
      };

      expect(event.prompt_token_ids).toEqual([1, 2, 3]);
      expect(event.response_token_ids).toEqual([4, 5, 6]);
    });
  });

  describe('UserRejectObservation', () => {
    it('should contain rejection information', () => {
      const event: UserRejectObservation = {
        id: generateEventId(),
        kind: 'UserRejectObservation',
        timestamp: new Date().toISOString(),
        source: 'user',
        action_id: 'action_789',
        tool_name: 'execute_command',
        tool_call_id: 'tool_call_789',
        rejection_reason: 'This action is too risky',
        rejection_source: 'user',
      };

      expect(event.action_id).toBe('action_789');
      expect(event.tool_name).toBe('execute_command');
      expect(event.tool_call_id).toBe('tool_call_789');
      expect(event.rejection_reason).toBe('This action is too risky');
      expect(event.rejection_source).toBe('user');
    });
  });
});

describe('generateEventId', () => {
  it('should generate unique IDs', () => {
    const ids = new Set<string>();
    for (let i = 0; i < 100; i++) {
      ids.add(generateEventId());
    }
    expect(ids.size).toBe(100);
  });

  it('should generate IDs with expected format', () => {
    const id = generateEventId();
    expect(id).toMatch(/^evt_\d+_[a-z0-9]+$/);
  });
});
