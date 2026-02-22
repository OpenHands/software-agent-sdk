/**
 * Tests for StuckDetector class
 *
 * These tests mirror the Python SDK's stuck detector tests to ensure
 * consistent behavior across implementations.
 */

import { StuckDetector, DEFAULT_STUCK_THRESHOLDS } from '../conversation/stuck-detector';
import {
  BaseEvent,
  MessageEvent,
  ActionEvent,
  ObservationEvent,
  AgentErrorEvent,
  generateEventId,
} from '../events/types';

// Helper functions to create test events
function createMessageEvent(source: 'user' | 'agent', text: string): MessageEvent {
  return {
    id: generateEventId(),
    kind: 'MessageEvent',
    timestamp: new Date().toISOString(),
    source,
    llm_message: {
      role: source === 'user' ? 'user' : 'assistant',
      content: [{ type: 'text', text }],
    },
  };
}

function createActionEvent(toolName: string, command: string, thought?: string): ActionEvent {
  return {
    id: generateEventId(),
    kind: 'ActionEvent',
    timestamp: new Date().toISOString(),
    source: 'agent',
    tool_name: toolName,
    tool_call_id: `call_${Math.random().toString(36).substr(2, 9)}`,
    action: { command },
    thought,
  };
}

function createObservationEvent(
  actionId: string,
  toolName: string,
  output: string
): ObservationEvent {
  return {
    id: generateEventId(),
    kind: 'ObservationEvent',
    timestamp: new Date().toISOString(),
    source: 'environment',
    tool_name: toolName,
    tool_call_id: `call_${Math.random().toString(36).substr(2, 9)}`,
    observation: { output },
    action_id: actionId,
  };
}

function createErrorEvent(_actionId: string, toolName: string, error: string): AgentErrorEvent {
  return {
    id: generateEventId(),
    kind: 'AgentErrorEvent',
    timestamp: new Date().toISOString(),
    source: 'agent',
    tool_name: toolName,
    tool_call_id: `call_${Math.random().toString(36).substr(2, 9)}`,
    error,
  };
}

describe('StuckDetector', () => {
  describe('Constructor and Defaults', () => {
    it('should use default thresholds when not provided', () => {
      const detector = new StuckDetector();
      // Access private thresholds through the isStuck behavior
      expect(detector).toBeDefined();
    });

    it('should accept custom thresholds', () => {
      const detector = new StuckDetector({
        actionObservation: 2,
        actionError: 2,
        monologue: 2,
        alternatingPattern: 4,
      });
      expect(detector).toBeDefined();
    });

    it('should merge partial thresholds with defaults', () => {
      const detector = new StuckDetector({ actionObservation: 10 });
      expect(detector).toBeDefined();
    });
  });

  describe('History Too Short', () => {
    it('should return not stuck when there are too few events', () => {
      const detector = new StuckDetector();
      const events: BaseEvent[] = [createMessageEvent('user', 'Hello')];

      const result = detector.isStuck(events);
      expect(result.isStuck).toBe(false);
    });

    it('should return not stuck when no user message is found', () => {
      const detector = new StuckDetector();
      const events: BaseEvent[] = [];

      const result = detector.isStuck(events);
      expect(result.isStuck).toBe(false);
    });

    it('should return not stuck with single action-observation pair', () => {
      const detector = new StuckDetector();
      const action = createActionEvent('terminal', 'ls');
      const events: BaseEvent[] = [
        createMessageEvent('user', 'Please run ls'),
        action,
        createObservationEvent(action.id, 'terminal', 'file1.txt\nfile2.txt'),
      ];

      const result = detector.isStuck(events);
      expect(result.isStuck).toBe(false);
    });
  });

  describe('Repeating Action-Observation Cycles', () => {
    it('should NOT be stuck with less than threshold repeats', () => {
      const detector = new StuckDetector({ actionObservation: 4 });
      const events: BaseEvent[] = [createMessageEvent('user', 'Please run ls')];

      // Add 3 identical action-observation pairs (threshold is 4)
      for (let i = 0; i < 3; i++) {
        const action = createActionEvent('terminal', 'ls', 'I need to run ls');
        events.push(action);
        events.push(createObservationEvent(action.id, 'terminal', 'file1.txt\nfile2.txt'));
      }

      const result = detector.isStuck(events);
      expect(result.isStuck).toBe(false);
    });

    it('should be stuck with threshold number of identical action-observation pairs', () => {
      const detector = new StuckDetector({ actionObservation: 4 });
      const events: BaseEvent[] = [createMessageEvent('user', 'Please run ls')];

      // Add 4 identical action-observation pairs
      for (let i = 0; i < 4; i++) {
        const action = createActionEvent('terminal', 'ls', 'I need to run ls');
        events.push(action);
        events.push(createObservationEvent(action.id, 'terminal', 'file1.txt\nfile2.txt'));
      }

      const result = detector.isStuck(events);
      expect(result.isStuck).toBe(true);
      expect(result.pattern).toBe('action_observation_loop');
      expect(result.repetitions).toBe(4);
    });
  });

  describe('Repeating Action-Error Cycles', () => {
    it('should NOT be stuck with less than threshold action-error pairs', () => {
      const detector = new StuckDetector({ actionError: 4 });
      const events: BaseEvent[] = [createMessageEvent('user', 'Run invalid command')];

      // Add 3 identical action-error pairs (threshold is 4)
      for (let i = 0; i < 3; i++) {
        const action = createActionEvent('terminal', 'invalid_cmd', 'Running command');
        events.push(action);
        events.push(createErrorEvent(action.id, 'terminal', 'Command not found'));
      }

      const result = detector.isStuck(events);
      expect(result.isStuck).toBe(false);
    });

    it('should be stuck with threshold number of identical action-error pairs', () => {
      const detector = new StuckDetector({ actionError: 4 });
      const events: BaseEvent[] = [createMessageEvent('user', 'Run invalid command')];

      // Add 4 identical action-error pairs
      for (let i = 0; i < 4; i++) {
        const action = createActionEvent('terminal', 'invalid_cmd', 'Running command');
        events.push(action);
        events.push(createErrorEvent(action.id, 'terminal', 'Command not found'));
      }

      const result = detector.isStuck(events);
      expect(result.isStuck).toBe(true);
      expect(result.pattern).toBe('action_error_loop');
    });
  });

  describe('Agent Monologue Detection', () => {
    it('should NOT be stuck with less than threshold consecutive agent messages', () => {
      const detector = new StuckDetector({ monologue: 4 });
      const events: BaseEvent[] = [
        createMessageEvent('user', 'Hello'),
        createMessageEvent('agent', 'Thinking...'),
        createMessageEvent('agent', 'Still thinking...'),
        createMessageEvent('agent', 'Almost done...'),
      ];

      const result = detector.isStuck(events);
      expect(result.isStuck).toBe(false);
    });

    it('should be stuck with threshold consecutive agent messages', () => {
      const detector = new StuckDetector({ monologue: 4 });
      const events: BaseEvent[] = [
        createMessageEvent('user', 'Hello'),
        createMessageEvent('agent', 'Thinking... 1'),
        createMessageEvent('agent', 'Thinking... 2'),
        createMessageEvent('agent', 'Thinking... 3'),
        createMessageEvent('agent', 'Thinking... 4'),
      ];

      const result = detector.isStuck(events);
      expect(result.isStuck).toBe(true);
      expect(result.pattern).toBe('monologue');
    });

    it('should NOT be stuck when user interrupts the monologue', () => {
      const detector = new StuckDetector({ monologue: 4 });
      const events: BaseEvent[] = [
        createMessageEvent('user', 'Hello'),
        createMessageEvent('agent', 'Thinking... 1'),
        createMessageEvent('agent', 'Thinking... 2'),
        createMessageEvent('user', 'Any progress?'), // User interrupts
        createMessageEvent('agent', 'Thinking... 3'),
        createMessageEvent('agent', 'Thinking... 4'),
      ];

      const result = detector.isStuck(events);
      expect(result.isStuck).toBe(false);
    });
  });

  describe('Different Actions (Not Stuck)', () => {
    it('should NOT be stuck when actions are different', () => {
      const detector = new StuckDetector();
      const events: BaseEvent[] = [createMessageEvent('user', 'Run some commands')];

      const commands = ['ls', 'pwd', 'whoami', 'date'];
      for (const cmd of commands) {
        const action = createActionEvent('terminal', cmd, `Running ${cmd}`);
        events.push(action);
        events.push(createObservationEvent(action.id, 'terminal', `output of ${cmd}`));
      }

      const result = detector.isStuck(events);
      expect(result.isStuck).toBe(false);
    });
  });

  describe('Reset After User Message', () => {
    it('should reset stuck detection after new user message', () => {
      const detector = new StuckDetector({ actionObservation: 4 });
      const events: BaseEvent[] = [createMessageEvent('user', 'Please run ls')];

      // Add 4 identical action-observation pairs (would trigger stuck)
      for (let i = 0; i < 4; i++) {
        const action = createActionEvent('terminal', 'ls', 'I need to run ls');
        events.push(action);
        events.push(createObservationEvent(action.id, 'terminal', 'file1.txt'));
      }

      // Verify it's stuck
      let result = detector.isStuck(events);
      expect(result.isStuck).toBe(true);

      // Add new user message
      events.push(createMessageEvent('user', 'Try something else'));

      // Should no longer be stuck
      result = detector.isStuck(events);
      expect(result.isStuck).toBe(false);
    });
  });

  describe('Alternating Pattern Detection', () => {
    it('should detect alternating A-B-A-B patterns', () => {
      const detector = new StuckDetector({ alternatingPattern: 6 });
      const events: BaseEvent[] = [createMessageEvent('user', 'Help me')];

      // Create alternating pattern: cmd1, cmd2, cmd1, cmd2, cmd1, cmd2
      for (let i = 0; i < 3; i++) {
        const action1 = createActionEvent('terminal', 'cmd1', 'Running cmd1');
        events.push(action1);
        events.push(createObservationEvent(action1.id, 'terminal', 'output1'));

        const action2 = createActionEvent('terminal', 'cmd2', 'Running cmd2');
        events.push(action2);
        events.push(createObservationEvent(action2.id, 'terminal', 'output2'));
      }

      const result = detector.isStuck(events);
      expect(result.isStuck).toBe(true);
      expect(result.pattern).toBe('alternating_pattern');
    });
  });

  describe('Default Thresholds', () => {
    it('should export DEFAULT_STUCK_THRESHOLDS', () => {
      expect(DEFAULT_STUCK_THRESHOLDS).toBeDefined();
      expect(DEFAULT_STUCK_THRESHOLDS.actionObservation).toBe(4);
      expect(DEFAULT_STUCK_THRESHOLDS.actionError).toBe(4);
      expect(DEFAULT_STUCK_THRESHOLDS.monologue).toBe(4);
      expect(DEFAULT_STUCK_THRESHOLDS.alternatingPattern).toBe(6);
    });
  });
});
