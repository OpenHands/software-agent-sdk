/**
 * Hook event types and data structures.
 *
 * Mirrors the Python SDK's openhands.sdk.hooks.types module.
 */

/**
 * Types of hook events that can trigger hooks.
 */
export enum HookEventType {
  PRE_TOOL_USE = 'PreToolUse',
  POST_TOOL_USE = 'PostToolUse',
  USER_PROMPT_SUBMIT = 'UserPromptSubmit',
  SESSION_START = 'SessionStart',
  SESSION_END = 'SessionEnd',
  STOP = 'Stop',
}

/**
 * Types of hooks that can be executed.
 */
export enum HookType {
  COMMAND = 'command',
  PROMPT = 'prompt',
}

/**
 * Decisions a hook can make about an operation.
 */
export enum HookDecision {
  ALLOW = 'allow',
  DENY = 'deny',
}

/**
 * Data passed to hook scripts via stdin as JSON.
 */
export interface HookEvent {
  event_type: HookEventType | string;
  tool_name?: string | null;
  tool_input?: Record<string, unknown> | null;
  tool_response?: Record<string, unknown> | null;
  message?: string | null;
  session_id?: string | null;
  working_dir?: string | null;
  metadata?: Record<string, unknown>;
}

/**
 * Result from executing a hook.
 *
 * Exit code 0 = success, exit code 2 = block operation.
 */
export interface HookResult {
  success: boolean;
  blocked: boolean;
  exit_code: number;
  stdout: string;
  stderr: string;
  decision?: HookDecision | null;
  reason?: string | null;
  additional_context?: string | null;
  error?: string | null;
  async_started?: boolean;
}

/**
 * Check whether the operation should continue after this hook result.
 */
export function hookResultShouldContinue(result: HookResult): boolean {
  if (result.blocked) return false;
  if (result.decision === HookDecision.DENY) return false;
  return true;
}

/**
 * Create a default successful HookResult.
 */
export function createSuccessResult(): HookResult {
  return {
    success: true,
    blocked: false,
    exit_code: 0,
    stdout: '',
    stderr: '',
    decision: null,
    reason: null,
    additional_context: null,
    error: null,
    async_started: false,
  };
}
