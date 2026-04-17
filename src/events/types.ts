/**
 * Rich event types for conversations
 *
 * These event types mirror the Python SDK's event system, providing
 * structured events for all conversation activities.
 */

import { Message, MessageContent, Event } from '../types/base';

/**
 * Event ID type - unique identifier for events
 */
export type EventID = string;

/**
 * Source of an event
 */
export type EventSource = 'agent' | 'user' | 'environment' | 'system' | 'hook';

/**
 * Base interface for all rich conversation events.
 * Extends the minimal Event interface from types/base.ts.
 */
export interface BaseEvent extends Event {
  /** Unique event identifier */
  id: EventID;
  /** Event type/kind discriminator */
  kind: string;
  /** ISO timestamp when event was created */
  timestamp: string;
  /** Source of the event */
  source?: EventSource;
}

/**
 * Message event - represents a message in the conversation
 */
export interface MessageEvent extends BaseEvent {
  kind: 'MessageEvent';
  /** The LLM message content */
  llm_message: Message;
  /** List of activated skills for this message */
  activated_skills?: string[];
  /** Optional sender identifier */
  sender?: string;
}

/**
 * Action event - represents an action taken by the agent
 */
export interface ActionEvent extends BaseEvent {
  kind: 'ActionEvent';
  /** The tool being called */
  tool_name: string;
  /** Tool call ID for correlation */
  tool_call_id: string;
  /** The action parameters/arguments */
  action: Record<string, unknown>;
  /** Agent's reasoning/thought for this action */
  thought?: string;
  /** LLM response ID that generated this action */
  llm_response_id?: string;
}

/**
 * Observation event - result of an action
 */
export interface ObservationEvent extends BaseEvent {
  kind: 'ObservationEvent';
  /** The tool that produced this observation */
  tool_name: string;
  /** Tool call ID for correlation with action */
  tool_call_id: string;
  /** The observation content/result */
  observation: unknown;
  /** ID of the action this observation corresponds to */
  action_id: string;
}

/**
 * Agent error event - error during agent execution (scaffold error, not tool result).
 * This IS sent to the LLM as a tool observation. Source is "agent".
 */
export interface AgentErrorEvent extends BaseEvent {
  kind: 'AgentErrorEvent';
  /** The tool that caused the error */
  tool_name: string;
  /** Tool call ID for correlation */
  tool_call_id: string;
  /** Error message from the scaffold */
  error: string;
}

/**
 * System prompt event - system prompt sent to LLM
 */
export interface SystemPromptEvent extends BaseEvent {
  kind: 'SystemPromptEvent';
  /** The system prompt content */
  system_prompt: MessageContent;
  /** Tools available to the agent */
  tools: unknown[];
}

/**
 * Pause event - agent execution paused
 */
export interface PauseEvent extends BaseEvent {
  kind: 'PauseEvent';
  /** Reason for pausing */
  reason?: string;
}

/**
 * Condensation request event - request to condense conversation history
 */
export interface CondensationRequestEvent extends BaseEvent {
  kind: 'CondensationRequest';
}

/**
 * Condensation summary event - the LLM-generated summary injected after condensation.
 * This IS sent to the LLM as context (LLMConvertibleEvent).
 */
export interface CondensationSummaryEvent extends BaseEvent {
  kind: 'CondensationSummaryEvent';
  /** The summary text of the condensed events */
  summary: string;
}

/**
 * Condensation event - marks that conversation history was condensed.
 * Records which events were forgotten and optionally includes a summary.
 * NOT sent to the LLM directly (infrastructure event).
 */
export interface CondensationEvent extends BaseEvent {
  kind: 'Condensation';
  /** IDs of events that were removed from context */
  forgotten_event_ids: string[];
  /** Summary of the forgotten events, if generated */
  summary?: string | null;
  /** Where to insert the summary in the view after removing forgotten events */
  summary_offset?: number | null;
  /** LLM response ID that triggered this condensation */
  llm_response_id: string;
}

/**
 * Conversation state update event - state change notification
 */
export interface ConversationStateUpdateEvent extends BaseEvent {
  kind: 'ConversationStateUpdateEvent';
  /** The state field that changed */
  key: string;
  /** New value of the field */
  value: unknown;
  /** Previous value (if available) */
  previous_value?: unknown;
}

/**
 * User reject observation - action was rejected by the user or a PreToolUse hook.
 * Sent to the LLM as an observation.
 */
export interface UserRejectObservation extends BaseEvent {
  kind: 'UserRejectObservation';
  /** The tool that was rejected */
  tool_name: string;
  /** Tool call ID for correlation with the action */
  tool_call_id: string;
  /** ID of the rejected action */
  action_id: string;
  /** Reason for rejection */
  rejection_reason: string;
  /** Source of the rejection */
  rejection_source: 'user' | 'system';
}

/**
 * Confirmation request event - action waiting for user confirmation
 */
export interface ConfirmationRequestEvent extends BaseEvent {
  kind: 'ConfirmationRequestEvent';
  /** ID of the action awaiting confirmation */
  action_id: string;
  /** The action details */
  action: ActionEvent;
  /** Risk level of the action */
  risk_level?: 'low' | 'medium' | 'high' | 'unknown';
  /** Risk assessment details */
  risk_assessment?: string;
}

/**
 * Confirmation response event - user response to confirmation request
 */
export interface ConfirmationResponseEvent extends BaseEvent {
  kind: 'ConfirmationResponseEvent';
  /** ID of the action being responded to */
  action_id: string;
  /** Whether the action was accepted */
  accepted: boolean;
  /** User's reason for the decision */
  reason?: string;
}

/**
 * Token event - raw token IDs from VLLM for LLM interaction tracking.
 * Carries prompt and response token ID arrays, not streaming text tokens.
 */
export interface TokenEvent extends BaseEvent {
  kind: 'TokenEvent';
  /** Token IDs from the prompt */
  prompt_token_ids: number[];
  /** Token IDs from the response */
  response_token_ids: number[];
}

/**
 * Stuck detection event - agent detected as stuck
 */
export interface StuckDetectionEvent extends BaseEvent {
  kind: 'StuckDetectionEvent';
  /** Type of stuck pattern detected */
  pattern:
    | 'action_observation_loop'
    | 'action_error_loop'
    | 'monologue'
    | 'alternating_pattern'
    | 'context_window_error';
  /** Number of repetitions detected */
  repetitions: number;
  /** Description of the stuck state */
  description: string;
}

/**
 * Finish event - agent finished the task
 */
export interface FinishEvent extends BaseEvent {
  kind: 'FinishEvent';
  /** Final message from the agent */
  message: string;
  /** Whether the task was completed successfully */
  success?: boolean;
}

/**
 * Think event - agent's internal reasoning
 */
export interface ThinkEvent extends BaseEvent {
  kind: 'ThinkEvent';
  /** The thought content */
  thought: string;
}

/**
 * Conversation error event - a conversation-level failure NOT sent to the LLM.
 * Typically causes the run loop to move to ERROR state. Source is usually "environment".
 */
export interface ConversationErrorEvent extends BaseEvent {
  kind: 'ConversationErrorEvent';
  /** Error code/type identifier */
  code: string;
  /** Detailed error message */
  detail: string;
}

/**
 * LLM completion log event - streams raw LLM completion logs from remote agents to clients.
 */
export interface LLMCompletionLogEvent extends BaseEvent {
  kind: 'LLMCompletionLogEvent';
  /** Intended filename for the log, relative to the log directory */
  filename: string;
  /** JSON-encoded log data */
  log_data: string;
  /** Name of the model that produced the log */
  model_name?: string;
  /** LLM usage_id that produced this log */
  usage_id?: string;
}

/**
 * Hook execution event type - matches Python SDK's HookEventType literal.
 */
export type HookExecutionEventType =
  | 'PreToolUse'
  | 'PostToolUse'
  | 'UserPromptSubmit'
  | 'SessionStart'
  | 'SessionEnd'
  | 'Stop';

/**
 * Hook execution event - emitted when a hook is executed.
 *
 * Provides observability into hook execution, including which hook type
 * was triggered, the command that was run, and the result.
 */
export interface HookExecutionEvent extends BaseEvent {
  kind: 'HookExecutionEvent';
  source: 'hook';
  /** The type of hook event that triggered this execution */
  hook_event_type: HookExecutionEventType;
  /** The hook command that was executed */
  hook_command: string;
  /** Tool name for PreToolUse/PostToolUse hooks */
  tool_name?: string | null;
  /** Whether the hook executed successfully */
  success: boolean;
  /** Whether the hook blocked the operation (exit code 2 or deny) */
  blocked: boolean;
  /** Exit code from the hook command */
  exit_code: number;
  /** Standard output from the hook */
  stdout: string;
  /** Standard error from the hook */
  stderr: string;
  /** Reason provided by hook (for blocking) */
  reason?: string | null;
  /** Additional context injected by hook (e.g., for UserPromptSubmit) */
  additional_context?: string | null;
  /** Error message if hook execution failed */
  error?: string | null;
  /** ID of the action this hook is associated with (PreToolUse/PostToolUse) */
  action_id?: string | null;
  /** ID of the message this hook is associated with (UserPromptSubmit) */
  message_id?: string | null;
  /** The input data that was passed to the hook */
  hook_input?: Record<string, unknown> | null;
}

/**
 * Union type of all conversation events
 */
export type ConversationEvent =
  | MessageEvent
  | ActionEvent
  | ObservationEvent
  | AgentErrorEvent
  | SystemPromptEvent
  | PauseEvent
  | CondensationRequestEvent
  | CondensationSummaryEvent
  | CondensationEvent
  | ConversationStateUpdateEvent
  | ConversationErrorEvent
  | LLMCompletionLogEvent
  | UserRejectObservation
  | ConfirmationRequestEvent
  | ConfirmationResponseEvent
  | TokenEvent
  | StuckDetectionEvent
  | FinishEvent
  | ThinkEvent
  | HookExecutionEvent;

/**
 * Type guard to check if an event is a MessageEvent
 */
export function isMessageEvent(event: BaseEvent): event is MessageEvent {
  return event.kind === 'MessageEvent';
}

/**
 * Type guard to check if an event is an ActionEvent
 */
export function isActionEvent(event: BaseEvent): event is ActionEvent {
  return event.kind === 'ActionEvent';
}

/**
 * Type guard to check if an event is an ObservationEvent
 */
export function isObservationEvent(event: BaseEvent): event is ObservationEvent {
  return event.kind === 'ObservationEvent';
}

/**
 * Type guard to check if an event is an AgentErrorEvent
 */
export function isAgentErrorEvent(event: BaseEvent): event is AgentErrorEvent {
  return event.kind === 'AgentErrorEvent';
}

/**
 * Type guard to check if event is observation-like (has action_id)
 */
export function isObservationLike(
  event: BaseEvent
): event is ObservationEvent | AgentErrorEvent | UserRejectObservation {
  return (
    event.kind === 'ObservationEvent' ||
    event.kind === 'AgentErrorEvent' ||
    event.kind === 'UserRejectObservation'
  );
}

/**
 * Type guard to check if an event is a ConversationErrorEvent
 */
export function isConversationErrorEvent(event: BaseEvent): event is ConversationErrorEvent {
  return event.kind === 'ConversationErrorEvent';
}

/**
 * Type guard to check if an event is a CondensationEvent
 */
export function isCondensationEvent(event: BaseEvent): event is CondensationEvent {
  return event.kind === 'Condensation';
}

/**
 * Type guard to check if an event is a HookExecutionEvent
 */
export function isHookExecutionEvent(event: BaseEvent): event is HookExecutionEvent {
  return event.kind === 'HookExecutionEvent';
}

/**
 * Generate a unique event ID
 */
export function generateEventId(): EventID {
  return `evt_${Date.now()}_${crypto.randomUUID().substring(0, 8)}`;
}

/**
 * Create a base event with common fields
 */
export function createBaseEvent(kind: string, source?: EventSource): BaseEvent {
  return {
    id: generateEventId(),
    kind,
    timestamp: new Date().toISOString(),
    source,
  };
}
