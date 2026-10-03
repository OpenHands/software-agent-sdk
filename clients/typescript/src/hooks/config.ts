/**
 * Hook configuration loading and management.
 *
 * Mirrors the Python SDK's openhands.sdk.hooks.config module.
 * Browser-safe: no file I/O operations (those happen server-side).
 */

import { HookEventType, HookType } from './types';

/**
 * Valid snake_case field names for hook events.
 * This is the single source of truth for hook event types.
 */
export const HOOK_EVENT_FIELDS: ReadonlySet<string> = new Set([
  'pre_tool_use',
  'post_tool_use',
  'user_prompt_submit',
  'session_start',
  'session_end',
  'stop',
]);

/**
 * A single hook definition.
 */
export interface HookDefinition {
  type?: HookType;
  command: string;
  timeout?: number;
  async?: boolean;
}

/**
 * Matches events to hooks based on patterns.
 *
 * Supports exact match, wildcard (*), and regex (auto-detected or /pattern/).
 */
export interface HookMatcher {
  matcher?: string;
  hooks: HookDefinition[];
}

/**
 * Configuration for all hooks.
 */
export interface HookConfig {
  pre_tool_use: HookMatcher[];
  post_tool_use: HookMatcher[];
  user_prompt_submit: HookMatcher[];
  session_start: HookMatcher[];
  session_end: HookMatcher[];
  stop: HookMatcher[];
  [key: string]: HookMatcher[];
}

/** Regex metacharacters that indicate a pattern should be treated as regex. */
const REGEX_METACHARACTERS = new Set('|.*+?[]()^$\\'.split(''));

/**
 * Check if a matcher matches the given tool name.
 */
export function matcherMatches(matcherDef: HookMatcher, toolName?: string | null): boolean {
  const pattern = matcherDef.matcher ?? '*';

  if (pattern === '*' || pattern === '') return true;
  if (toolName == null) return pattern === '*' || pattern === '';

  // Explicit regex: /pattern/
  if (pattern.startsWith('/') && pattern.endsWith('/') && pattern.length > 2) {
    const regexStr = pattern.slice(1, -1);
    try {
      return new RegExp(`^${regexStr}$`).test(toolName);
    } catch {
      return false;
    }
  }

  // Auto-detect regex: contains metacharacters
  if ([...pattern].some((c) => REGEX_METACHARACTERS.has(c))) {
    try {
      return new RegExp(`^${pattern}$`).test(toolName);
    } catch {
      // Invalid regex, fall through to exact match
    }
  }

  return pattern === toolName;
}

/**
 * Create an empty HookConfig.
 */
export function createEmptyHookConfig(): HookConfig {
  return {
    pre_tool_use: [],
    post_tool_use: [],
    user_prompt_submit: [],
    session_start: [],
    session_end: [],
    stop: [],
  };
}

/**
 * Check if a HookConfig has no hooks configured.
 */
export function isHookConfigEmpty(config: HookConfig): boolean {
  return ![
    config.pre_tool_use,
    config.post_tool_use,
    config.user_prompt_submit,
    config.session_start,
    config.session_end,
    config.stop,
  ].some((list) => list.length > 0);
}

/** Convert PascalCase to snake_case. */
function pascalToSnake(name: string): string {
  return name.replace(/(?<!^)(?=[A-Z])/g, '_').toLowerCase();
}

/**
 * Normalize raw hooks data, supporting PascalCase keys and the legacy `{hooks: ...}` wrapper.
 */
export function normalizeHooksInput(data: Record<string, unknown>): Record<string, unknown> {
  let unwrapped = data;

  // Unwrap legacy format: {"hooks": {"PreToolUse": [...]}}
  if ('hooks' in unwrapped && typeof unwrapped.hooks === 'object' && unwrapped.hooks !== null) {
    unwrapped = unwrapped.hooks as Record<string, unknown>;
  }

  const normalized: Record<string, unknown> = {};
  const seenFields = new Set<string>();

  for (const [key, value] of Object.entries(unwrapped)) {
    const snakeKey = pascalToSnake(key);
    const isPascalCase = snakeKey !== key;

    if (isPascalCase && !HOOK_EVENT_FIELDS.has(snakeKey)) {
      const validTypes = [...HOOK_EVENT_FIELDS].sort().join(', ');
      throw new Error(`Unknown event type '${key}'. Valid types: ${validTypes}`);
    }

    if (seenFields.has(snakeKey)) {
      throw new Error(
        `Duplicate hook event: both '${key}' and its snake_case equivalent '${snakeKey}' were provided`
      );
    }

    seenFields.add(snakeKey);
    normalized[snakeKey] = value;
  }

  return normalized;
}

/**
 * Create a HookConfig from a raw data object.
 * Supports both PascalCase and snake_case keys, and the legacy "hooks" wrapper.
 */
export function hookConfigFromData(data: Record<string, unknown>): HookConfig {
  const normalized = normalizeHooksInput(data);
  const config = createEmptyHookConfig();

  for (const field of HOOK_EVENT_FIELDS) {
    if (field in normalized && Array.isArray(normalized[field])) {
      config[field] = normalized[field] as HookMatcher[];
    }
  }

  return config;
}

/**
 * Get all hook definitions that match a given event type and optional tool name.
 */
export function getHooksForEvent(
  config: HookConfig,
  eventType: HookEventType,
  toolName?: string | null
): HookDefinition[] {
  const fieldName = pascalToSnake(eventType);
  const matchers: HookMatcher[] = config[fieldName] ?? [];
  const result: HookDefinition[] = [];

  for (const matcher of matchers) {
    if (matcherMatches(matcher, toolName)) {
      result.push(...matcher.hooks);
    }
  }

  return result;
}

/**
 * Check if there are any hooks configured for an event type.
 */
export function hasHooksForEvent(config: HookConfig, eventType: HookEventType): boolean {
  const fieldName = pascalToSnake(eventType);
  const matchers: HookMatcher[] = config[fieldName] ?? [];
  return matchers.length > 0;
}

/**
 * Merge multiple hook configs by concatenating handlers per event type.
 * Returns null if the result is empty.
 */
export function mergeHookConfigs(configs: HookConfig[]): HookConfig | null {
  if (configs.length === 0) return null;

  const merged = createEmptyHookConfig();
  for (const config of configs) {
    for (const field of HOOK_EVENT_FIELDS) {
      merged[field] = [...merged[field], ...config[field]];
    }
  }

  if (isHookConfigEmpty(merged)) return null;
  return merged;
}

/**
 * Serialize a HookConfig to a plain object suitable for JSON/API payloads.
 * Only includes non-empty fields.
 */
export function hookConfigToJSON(config: HookConfig): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const field of HOOK_EVENT_FIELDS) {
    if (config[field].length > 0) {
      result[field] = config[field];
    }
  }
  return result;
}
