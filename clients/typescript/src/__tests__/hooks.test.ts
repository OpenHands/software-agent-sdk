import {
  HookEventType,
  HookType,
  HookDecision,
  hookResultShouldContinue,
  createSuccessResult,
  HOOK_EVENT_FIELDS,
  matcherMatches,
  createEmptyHookConfig,
  isHookConfigEmpty,
  normalizeHooksInput,
  hookConfigFromData,
  getHooksForEvent,
  hasHooksForEvent,
  mergeHookConfigs,
  hookConfigToJSON,
  isHookExecutionEvent,
} from '../index';
import type { HookMatcher, HookResult } from '../index';

describe('Hooks Types', () => {
  describe('HookEventType', () => {
    it('should have all expected values', () => {
      expect(HookEventType.PRE_TOOL_USE).toBe('PreToolUse');
      expect(HookEventType.POST_TOOL_USE).toBe('PostToolUse');
      expect(HookEventType.USER_PROMPT_SUBMIT).toBe('UserPromptSubmit');
      expect(HookEventType.SESSION_START).toBe('SessionStart');
      expect(HookEventType.SESSION_END).toBe('SessionEnd');
      expect(HookEventType.STOP).toBe('Stop');
    });
  });

  describe('HookType', () => {
    it('should have all expected values', () => {
      expect(HookType.COMMAND).toBe('command');
      expect(HookType.PROMPT).toBe('prompt');
    });
  });

  describe('HookDecision', () => {
    it('should have all expected values', () => {
      expect(HookDecision.ALLOW).toBe('allow');
      expect(HookDecision.DENY).toBe('deny');
    });
  });

  describe('hookResultShouldContinue', () => {
    it('should return true for successful result', () => {
      expect(hookResultShouldContinue(createSuccessResult())).toBe(true);
    });

    it('should return false for blocked result', () => {
      const result: HookResult = { ...createSuccessResult(), blocked: true };
      expect(hookResultShouldContinue(result)).toBe(false);
    });

    it('should return false for DENY decision', () => {
      const result: HookResult = {
        ...createSuccessResult(),
        decision: HookDecision.DENY,
      };
      expect(hookResultShouldContinue(result)).toBe(false);
    });

    it('should return true for ALLOW decision', () => {
      const result: HookResult = {
        ...createSuccessResult(),
        decision: HookDecision.ALLOW,
      };
      expect(hookResultShouldContinue(result)).toBe(true);
    });
  });

  describe('createSuccessResult', () => {
    it('should create a default success result', () => {
      const result = createSuccessResult();
      expect(result.success).toBe(true);
      expect(result.blocked).toBe(false);
      expect(result.exit_code).toBe(0);
      expect(result.stdout).toBe('');
      expect(result.stderr).toBe('');
      expect(result.async_started).toBe(false);
    });
  });
});

describe('Hooks Config', () => {
  describe('HOOK_EVENT_FIELDS', () => {
    it('should contain all expected fields', () => {
      expect(HOOK_EVENT_FIELDS.has('pre_tool_use')).toBe(true);
      expect(HOOK_EVENT_FIELDS.has('post_tool_use')).toBe(true);
      expect(HOOK_EVENT_FIELDS.has('user_prompt_submit')).toBe(true);
      expect(HOOK_EVENT_FIELDS.has('session_start')).toBe(true);
      expect(HOOK_EVENT_FIELDS.has('session_end')).toBe(true);
      expect(HOOK_EVENT_FIELDS.has('stop')).toBe(true);
      expect(HOOK_EVENT_FIELDS.size).toBe(6);
    });
  });

  describe('matcherMatches', () => {
    it('should match wildcard to everything', () => {
      const matcher: HookMatcher = { matcher: '*', hooks: [] };
      expect(matcherMatches(matcher, 'terminal')).toBe(true);
      expect(matcherMatches(matcher, 'anything')).toBe(true);
      expect(matcherMatches(matcher, null)).toBe(true);
    });

    it('should match empty matcher to everything', () => {
      const matcher: HookMatcher = { matcher: '', hooks: [] };
      expect(matcherMatches(matcher, 'terminal')).toBe(true);
    });

    it('should match default (undefined) matcher to everything', () => {
      const matcher: HookMatcher = { hooks: [] };
      expect(matcherMatches(matcher, 'terminal')).toBe(true);
    });

    it('should do exact match', () => {
      const matcher: HookMatcher = { matcher: 'terminal', hooks: [] };
      expect(matcherMatches(matcher, 'terminal')).toBe(true);
      expect(matcherMatches(matcher, 'other')).toBe(false);
    });

    it('should support explicit regex /pattern/', () => {
      const matcher: HookMatcher = { matcher: '/term.*/', hooks: [] };
      expect(matcherMatches(matcher, 'terminal')).toBe(true);
      expect(matcherMatches(matcher, 'other')).toBe(false);
    });

    it('should auto-detect regex with metacharacters', () => {
      const matcher: HookMatcher = { matcher: 'term.*', hooks: [] };
      expect(matcherMatches(matcher, 'terminal')).toBe(true);
      expect(matcherMatches(matcher, 'other')).toBe(false);
    });

    it('should handle invalid regex gracefully', () => {
      const matcher: HookMatcher = { matcher: '/[invalid/', hooks: [] };
      expect(matcherMatches(matcher, 'anything')).toBe(false);
    });

    it('should not match null toolName for non-wildcard', () => {
      const matcher: HookMatcher = { matcher: 'terminal', hooks: [] };
      expect(matcherMatches(matcher, null)).toBe(false);
    });
  });

  describe('createEmptyHookConfig', () => {
    it('should create config with all empty arrays', () => {
      const config = createEmptyHookConfig();
      expect(config.pre_tool_use).toEqual([]);
      expect(config.post_tool_use).toEqual([]);
      expect(config.user_prompt_submit).toEqual([]);
      expect(config.session_start).toEqual([]);
      expect(config.session_end).toEqual([]);
      expect(config.stop).toEqual([]);
    });
  });

  describe('isHookConfigEmpty', () => {
    it('should return true for empty config', () => {
      expect(isHookConfigEmpty(createEmptyHookConfig())).toBe(true);
    });

    it('should return false for config with hooks', () => {
      const config = createEmptyHookConfig();
      config.stop = [{ matcher: '*', hooks: [{ command: 'echo hello' }] }];
      expect(isHookConfigEmpty(config)).toBe(false);
    });
  });

  describe('normalizeHooksInput', () => {
    it('should unwrap legacy format with hooks wrapper', () => {
      const data = {
        hooks: {
          PreToolUse: [{ matcher: '*', hooks: [{ command: 'echo test' }] }],
        },
      };
      const normalized = normalizeHooksInput(data);
      expect(normalized.pre_tool_use).toBeDefined();
      expect(normalized['PreToolUse']).toBeUndefined();
    });

    it('should convert PascalCase to snake_case', () => {
      const data = {
        PreToolUse: [{ matcher: '*', hooks: [{ command: 'echo test' }] }],
        PostToolUse: [{ matcher: '*', hooks: [{ command: 'echo test' }] }],
      };
      const normalized = normalizeHooksInput(data);
      expect(normalized.pre_tool_use).toBeDefined();
      expect(normalized.post_tool_use).toBeDefined();
    });

    it('should pass through snake_case keys unchanged', () => {
      const data = {
        pre_tool_use: [{ matcher: '*', hooks: [{ command: 'echo test' }] }],
      };
      const normalized = normalizeHooksInput(data);
      expect(normalized.pre_tool_use).toBeDefined();
    });

    it('should throw on unknown event type', () => {
      const data = { UnknownEvent: [] };
      expect(() => normalizeHooksInput(data)).toThrow('Unknown event type');
    });

    it('should throw on duplicate keys', () => {
      const data = {
        pre_tool_use: [],
        PreToolUse: [],
      };
      expect(() => normalizeHooksInput(data)).toThrow('Duplicate hook event');
    });
  });

  describe('hookConfigFromData', () => {
    it('should create config from PascalCase data', () => {
      const data = {
        PreToolUse: [{ matcher: 'terminal', hooks: [{ command: 'echo block', type: 'command' }] }],
        Stop: [{ matcher: '*', hooks: [{ command: 'echo stop' }] }],
      };
      const config = hookConfigFromData(data);
      expect(config.pre_tool_use).toHaveLength(1);
      expect(config.stop).toHaveLength(1);
      expect(config.post_tool_use).toHaveLength(0);
    });

    it('should create config from legacy wrapper format', () => {
      const data = {
        hooks: {
          stop: [{ matcher: '*', hooks: [{ command: 'echo stop' }] }],
        },
      };
      const config = hookConfigFromData(data);
      expect(config.stop).toHaveLength(1);
    });
  });

  describe('getHooksForEvent', () => {
    it('should return matching hooks for event type', () => {
      const config = createEmptyHookConfig();
      config.pre_tool_use = [
        { matcher: 'terminal', hooks: [{ command: 'echo block' }] },
        { matcher: 'other', hooks: [{ command: 'echo other' }] },
      ];

      const hooks = getHooksForEvent(config, HookEventType.PRE_TOOL_USE, 'terminal');
      expect(hooks).toHaveLength(1);
      expect(hooks[0].command).toBe('echo block');
    });

    it('should return all matching hooks with wildcard', () => {
      const config = createEmptyHookConfig();
      config.pre_tool_use = [
        { matcher: '*', hooks: [{ command: 'echo all' }] },
        { matcher: 'terminal', hooks: [{ command: 'echo terminal' }] },
      ];

      const hooks = getHooksForEvent(config, HookEventType.PRE_TOOL_USE, 'terminal');
      expect(hooks).toHaveLength(2);
    });

    it('should return empty for no matches', () => {
      const config = createEmptyHookConfig();
      config.pre_tool_use = [{ matcher: 'other', hooks: [{ command: 'echo other' }] }];

      const hooks = getHooksForEvent(config, HookEventType.PRE_TOOL_USE, 'terminal');
      expect(hooks).toHaveLength(0);
    });

    it('should return empty for unconfigured event type', () => {
      const config = createEmptyHookConfig();
      const hooks = getHooksForEvent(config, HookEventType.SESSION_START);
      expect(hooks).toHaveLength(0);
    });
  });

  describe('hasHooksForEvent', () => {
    it('should return true when hooks are configured', () => {
      const config = createEmptyHookConfig();
      config.stop = [{ matcher: '*', hooks: [{ command: 'echo stop' }] }];
      expect(hasHooksForEvent(config, HookEventType.STOP)).toBe(true);
    });

    it('should return false when no hooks configured', () => {
      const config = createEmptyHookConfig();
      expect(hasHooksForEvent(config, HookEventType.STOP)).toBe(false);
    });
  });

  describe('mergeHookConfigs', () => {
    it('should return null for empty array', () => {
      expect(mergeHookConfigs([])).toBeNull();
    });

    it('should return null for all-empty configs', () => {
      expect(mergeHookConfigs([createEmptyHookConfig()])).toBeNull();
    });

    it('should merge configs from multiple sources', () => {
      const config1 = createEmptyHookConfig();
      config1.pre_tool_use = [{ matcher: '*', hooks: [{ command: 'echo 1' }] }];

      const config2 = createEmptyHookConfig();
      config2.pre_tool_use = [{ matcher: 'terminal', hooks: [{ command: 'echo 2' }] }];
      config2.stop = [{ matcher: '*', hooks: [{ command: 'echo stop' }] }];

      const merged = mergeHookConfigs([config1, config2]);
      expect(merged).not.toBeNull();
      expect(merged!.pre_tool_use).toHaveLength(2);
      expect(merged!.stop).toHaveLength(1);
    });
  });

  describe('hookConfigToJSON', () => {
    it('should only include non-empty fields', () => {
      const config = createEmptyHookConfig();
      config.stop = [{ matcher: '*', hooks: [{ command: 'echo stop' }] }];

      const json = hookConfigToJSON(config);
      expect(json.stop).toBeDefined();
      expect(json.pre_tool_use).toBeUndefined();
      expect(json.post_tool_use).toBeUndefined();
    });
  });
});

describe('HookExecutionEvent', () => {
  describe('isHookExecutionEvent', () => {
    it('should return true for hook execution events', () => {
      const event = {
        id: 'evt_123',
        kind: 'HookExecutionEvent',
        timestamp: '2024-01-01T00:00:00Z',
        source: 'hook' as const,
        hook_event_type: 'PreToolUse' as const,
        hook_command: 'echo test',
        success: true,
        blocked: false,
        exit_code: 0,
        stdout: '',
        stderr: '',
      };
      expect(isHookExecutionEvent(event)).toBe(true);
    });

    it('should return false for other event kinds', () => {
      const event = {
        id: 'evt_123',
        kind: 'MessageEvent',
        timestamp: '2024-01-01T00:00:00Z',
      };
      expect(isHookExecutionEvent(event)).toBe(false);
    });
  });
});
