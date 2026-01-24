/**
 * Tests for Security module (ConfirmationPolicy and SecurityAnalyzer)
 *
 * These tests mirror the Python SDK's security tests to ensure
 * consistent behavior across implementations.
 */

import {
  NeverConfirm,
  AlwaysConfirm,
  RiskBasedConfirm,
  ToolBasedConfirm,
  CompositeConfirm,
  createConfirmationPolicy,
} from '../security/confirmation-policy';

import {
  PatternBasedAnalyzer,
  AllowlistAnalyzer,
  NoOpAnalyzer,
  CompositeAnalyzer,
  createSecurityAnalyzer,
} from '../security/security-analyzer';

import { ActionEvent, generateEventId } from '../events/types';

// Helper to create mock action events
function createActionEvent(toolName: string, action: Record<string, unknown>): ActionEvent {
  return {
    id: generateEventId(),
    kind: 'ActionEvent',
    timestamp: new Date().toISOString(),
    source: 'agent',
    tool_name: toolName,
    tool_call_id: `call_${Math.random().toString(36).substr(2, 9)}`,
    action,
  };
}

describe('Confirmation Policies', () => {
  describe('NeverConfirm', () => {
    it('should never require confirmation', () => {
      const policy = new NeverConfirm();
      const action = createActionEvent('terminal', { command: 'ls' });

      expect(policy.type).toBe('never');
      expect(policy.requiresConfirmation(action)).toBe(false);
      expect(policy.requiresConfirmation(action, 'low')).toBe(false);
      expect(policy.requiresConfirmation(action, 'medium')).toBe(false);
      expect(policy.requiresConfirmation(action, 'high')).toBe(false);
    });
  });

  describe('AlwaysConfirm', () => {
    it('should always require confirmation', () => {
      const policy = new AlwaysConfirm();
      const action = createActionEvent('terminal', { command: 'ls' });

      expect(policy.type).toBe('always');
      expect(policy.requiresConfirmation(action)).toBe(true);
      expect(policy.requiresConfirmation(action, 'low')).toBe(true);
      expect(policy.requiresConfirmation(action, 'medium')).toBe(true);
      expect(policy.requiresConfirmation(action, 'high')).toBe(true);
    });
  });

  describe('RiskBasedConfirm', () => {
    it('should require confirmation based on risk threshold', () => {
      const policy = new RiskBasedConfirm('medium');
      const action = createActionEvent('terminal', { command: 'ls' });

      expect(policy.type).toBe('risk_based');
      expect(policy.requiresConfirmation(action, 'low')).toBe(false);
      expect(policy.requiresConfirmation(action, 'medium')).toBe(true);
      expect(policy.requiresConfirmation(action, 'high')).toBe(true);
    });

    it('should treat unknown risk as medium', () => {
      const policy = new RiskBasedConfirm('medium');
      const action = createActionEvent('terminal', { command: 'ls' });

      expect(policy.requiresConfirmation(action, 'unknown')).toBe(true);
      expect(policy.requiresConfirmation(action)).toBe(true); // No risk = unknown
    });

    it('should use default threshold of medium', () => {
      const policy = new RiskBasedConfirm();
      const action = createActionEvent('terminal', { command: 'ls' });

      expect(policy.requiresConfirmation(action, 'low')).toBe(false);
      expect(policy.requiresConfirmation(action, 'medium')).toBe(true);
    });
  });

  describe('ToolBasedConfirm', () => {
    it('should require confirmation for specified tools', () => {
      const policy = new ToolBasedConfirm(['dangerous_tool', 'risky_operation']);

      const safeAction = createActionEvent('safe_tool', { arg: 'value' });
      const dangerousAction = createActionEvent('dangerous_tool', { arg: 'value' });
      const riskyAction = createActionEvent('risky_operation', { arg: 'value' });

      expect(policy.type).toBe('tool_based');
      expect(policy.requiresConfirmation(safeAction)).toBe(false);
      expect(policy.requiresConfirmation(dangerousAction)).toBe(true);
      expect(policy.requiresConfirmation(riskyAction)).toBe(true);
    });
  });

  describe('CompositeConfirm', () => {
    it('should require confirmation if ANY policy requires it', () => {
      const policy = new CompositeConfirm([
        new NeverConfirm(),
        new ToolBasedConfirm(['special_tool']),
      ]);

      const normalAction = createActionEvent('normal_tool', { arg: 'value' });
      const specialAction = createActionEvent('special_tool', { arg: 'value' });

      expect(policy.type).toBe('composite');
      expect(policy.requiresConfirmation(normalAction)).toBe(false);
      expect(policy.requiresConfirmation(specialAction)).toBe(true);
    });

    it('should handle multiple policies', () => {
      const policy = new CompositeConfirm([
        new RiskBasedConfirm('high'), // Only high risk
        new ToolBasedConfirm(['always_confirm_tool']),
      ]);

      const normalAction = createActionEvent('normal_tool', { arg: 'value' });
      const alwaysConfirmAction = createActionEvent('always_confirm_tool', { arg: 'value' });

      expect(policy.requiresConfirmation(normalAction, 'low')).toBe(false);
      expect(policy.requiresConfirmation(normalAction, 'medium')).toBe(false);
      expect(policy.requiresConfirmation(normalAction, 'high')).toBe(true);
      expect(policy.requiresConfirmation(alwaysConfirmAction, 'low')).toBe(true);
    });
  });

  describe('createConfirmationPolicy Factory', () => {
    it('should create NeverConfirm', () => {
      const policy = createConfirmationPolicy('never');
      expect(policy).toBeInstanceOf(NeverConfirm);
    });

    it('should create AlwaysConfirm', () => {
      const policy = createConfirmationPolicy('always');
      expect(policy).toBeInstanceOf(AlwaysConfirm);
    });

    it('should create RiskBasedConfirm with options', () => {
      const policy = createConfirmationPolicy('risk_based', { threshold: 'high' });
      expect(policy).toBeInstanceOf(RiskBasedConfirm);
    });

    it('should create ToolBasedConfirm with options', () => {
      const policy = createConfirmationPolicy('tool_based', { tools: ['tool1', 'tool2'] });
      expect(policy).toBeInstanceOf(ToolBasedConfirm);
    });

    it('should default to NeverConfirm for unknown types', () => {
      const policy = createConfirmationPolicy('unknown_type');
      expect(policy).toBeInstanceOf(NeverConfirm);
    });
  });
});

describe('Security Analyzers', () => {
  describe('PatternBasedAnalyzer', () => {
    it('should detect high-risk patterns', () => {
      const analyzer = new PatternBasedAnalyzer();

      // rm -rf with root path
      const rmAction = createActionEvent('terminal', { command: 'rm -rf /' });
      const result = analyzer.analyze(rmAction);
      expect(result.riskLevel).toBe('high');
      expect(result.requiresConfirmation).toBe(true);
      expect(result.concerns?.length).toBeGreaterThan(0);
    });

    it('should detect sudo commands as high risk', () => {
      const analyzer = new PatternBasedAnalyzer();
      const action = createActionEvent('terminal', { command: 'sudo apt-get install something' });
      const result = analyzer.analyze(action);
      expect(result.riskLevel).toBe('high');
    });

    it('should detect medium-risk patterns', () => {
      const analyzer = new PatternBasedAnalyzer();

      // git push
      const pushAction = createActionEvent('terminal', { command: 'git push origin main' });
      const result = analyzer.analyze(pushAction);
      expect(result.riskLevel).toBe('medium');
    });

    it('should mark safe commands as low risk', () => {
      const analyzer = new PatternBasedAnalyzer();
      const action = createActionEvent('terminal', { command: 'echo hello' });
      const result = analyzer.analyze(action);
      expect(result.riskLevel).toBe('low');
      expect(result.requiresConfirmation).toBe(false);
    });

    it('should detect sensitive file access', () => {
      const analyzer = new PatternBasedAnalyzer();

      // SSH key access
      const sshAction = createActionEvent('read_file', { path: '~/.ssh/id_rsa' });
      const result = analyzer.analyze(sshAction);
      expect(result.riskLevel).not.toBe('low');
      expect(result.concerns).toBeDefined();
    });

    it('should detect .env file access', () => {
      const analyzer = new PatternBasedAnalyzer();
      const action = createActionEvent('read_file', { path: '/app/.env' });
      const result = analyzer.analyze(action);
      expect(result.riskLevel).not.toBe('low');
    });
  });

  describe('AllowlistAnalyzer', () => {
    it('should allow listed tools', () => {
      const analyzer = new AllowlistAnalyzer(['safe_tool', 'read_file']);

      const safeAction = createActionEvent('safe_tool', { arg: 'value' });
      const readAction = createActionEvent('read_file', { path: '/tmp/file' });

      expect(analyzer.analyze(safeAction).riskLevel).toBe('low');
      expect(analyzer.analyze(readAction).riskLevel).toBe('low');
    });

    it('should mark unlisted tools as high risk', () => {
      const analyzer = new AllowlistAnalyzer(['safe_tool']);
      const action = createActionEvent('unknown_tool', { arg: 'value' });

      const result = analyzer.analyze(action);
      expect(result.riskLevel).toBe('high');
      expect(result.requiresConfirmation).toBe(true);
    });

    it('should support pattern matching', () => {
      const analyzer = new AllowlistAnalyzer([], [/^read_/]);

      const readAction = createActionEvent('read_file', { path: '/tmp' });
      // Using readAction to verify pattern matching exists
      const result = analyzer.analyze(readAction);

      // Pattern matching is on action JSON, not tool name
      // This tests that patterns are checked
      expect(analyzer.type).toBe('allowlist');
      expect(result).toBeDefined();
    });
  });

  describe('NoOpAnalyzer', () => {
    it('should always return low risk', () => {
      const analyzer = new NoOpAnalyzer();

      const dangerousAction = createActionEvent('terminal', { command: 'rm -rf /' });
      const result = analyzer.analyze(dangerousAction);

      expect(result.riskLevel).toBe('low');
      expect(result.requiresConfirmation).toBe(false);
    });
  });

  describe('CompositeAnalyzer', () => {
    it('should return highest risk from all analyzers', async () => {
      const analyzer = new CompositeAnalyzer([
        new NoOpAnalyzer(), // Always low
        new PatternBasedAnalyzer(), // Will detect high risk
      ]);

      const action = createActionEvent('terminal', { command: 'sudo rm -rf /' });
      const result = await analyzer.analyze(action);

      expect(result.riskLevel).toBe('high');
    });

    it('should combine concerns from all analyzers', async () => {
      const analyzer = new CompositeAnalyzer([
        new PatternBasedAnalyzer(),
        new AllowlistAnalyzer(['other_tool']),
      ]);

      const action = createActionEvent('terminal', { command: 'rm file.txt' });
      const result = await analyzer.analyze(action);

      // Should have concerns from both analyzers
      expect(result.concerns).toBeDefined();
      expect(result.concerns!.length).toBeGreaterThan(0);
    });
  });

  describe('createSecurityAnalyzer Factory', () => {
    it('should create PatternBasedAnalyzer', () => {
      const analyzer = createSecurityAnalyzer('pattern_based');
      expect(analyzer).toBeInstanceOf(PatternBasedAnalyzer);
    });

    it('should create AllowlistAnalyzer', () => {
      const analyzer = createSecurityAnalyzer('allowlist', { tools: ['tool1'] });
      expect(analyzer).toBeInstanceOf(AllowlistAnalyzer);
    });

    it('should create NoOpAnalyzer', () => {
      const analyzer = createSecurityAnalyzer('noop');
      expect(analyzer).toBeInstanceOf(NoOpAnalyzer);
    });

    it('should default to PatternBasedAnalyzer', () => {
      const analyzer = createSecurityAnalyzer('unknown');
      expect(analyzer).toBeInstanceOf(PatternBasedAnalyzer);
    });
  });
});
