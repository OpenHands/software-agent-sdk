/**
 * OpenHands Hooks System - Event-driven hooks for automation and control.
 *
 * Hooks are event-driven scripts that execute at specific lifecycle events
 * during agent execution, enabling deterministic control over agent behavior.
 *
 * Hook execution happens server-side. This module provides:
 * - Type definitions matching the Python SDK
 * - Configuration parsing and manipulation utilities
 * - API client methods for loading hooks from the server
 */

export {
  HookEventType,
  HookType,
  HookDecision,
  hookResultShouldContinue,
  createSuccessResult,
} from './types';
export type { HookEvent, HookResult } from './types';

export {
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
} from './config';
export type { HookDefinition, HookMatcher, HookConfig } from './config';
