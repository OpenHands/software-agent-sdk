/**
 * Rich event types for conversations
 *
 * These event types mirror the Python SDK's event system, providing
 * structured events for all conversation activities.
 */

import type {
  AcpToolCallEvent as AgentServerAcpToolCallEvent,
  ActionEvent as AgentServerActionEvent,
  AgentErrorEvent as AgentServerAgentErrorEvent,
  Condensation as AgentServerCondensationEvent,
  CondensationRequest as AgentServerCondensationRequestEvent,
  CondensationSummaryEvent as AgentServerCondensationSummaryEvent,
  ConversationErrorEvent as AgentServerConversationErrorEvent,
  ConversationStateUpdateEvent as AgentServerConversationStateUpdateEvent,
  ErrorClassification as AgentServerErrorClassification,
  Event as AgentServerEvent,
  HookExecutionEvent as AgentServerHookExecutionEvent,
  LlmCompletionLogEvent as AgentServerLlmCompletionLogEvent,
  MessageEvent as AgentServerMessageEvent,
  MessageToolCall as AgentServerMessageToolCall,
  ObservationEvent as AgentServerObservationEvent,
  PauseEvent as AgentServerPauseEvent,
  SecurityRisk as AgentServerSecurityRisk,
  StreamingDeltaEvent as AgentServerStreamingDeltaEvent,
  SystemPromptEvent as AgentServerSystemPromptEvent,
  TokenEvent as AgentServerTokenEvent,
  ToolDefinition as AgentServerToolDefinition,
  UserRejectObservation as AgentServerUserRejectObservation,
} from '../generated/agent-server-schema';

export type ACPToolCallEvent = AgentServerAcpToolCallEvent;
export type ACPToolCallStatus = NonNullable<ACPToolCallEvent['status']>;
export type ACPToolKind = NonNullable<ACPToolCallEvent['tool_kind']>;
/**
 * The local conversation implementation represents tool inputs as opaque JSON.
 * Keep that client-side convenience while sourcing every other ActionEvent field
 * from the Agent Server schema.
 */
export type ActionEvent = Omit<
  AgentServerActionEvent,
  'action' | 'llm_response_id' | 'thought' | 'tool_call'
> & {
  action: Record<string, unknown> | null;
  llm_response_id?: EventID;
  thought?: AgentServerActionEvent['thought'] | string;
  tool_call?: AgentServerMessageToolCall;
};
export type AgentErrorEvent = AgentServerAgentErrorEvent;
export type CondensationEvent = AgentServerCondensationEvent;
export type CondensationRequestEvent = AgentServerCondensationRequestEvent;
export type CondensationSummaryEvent = AgentServerCondensationSummaryEvent;
export type ConversationErrorEvent = Omit<AgentServerConversationErrorEvent, 'source'> & {
  source?: EventSource;
};
export type ConversationStateUpdateEvent = AgentServerConversationStateUpdateEvent;
export type ErrorClassification = AgentServerErrorClassification;
export type HookExecutionEvent = AgentServerHookExecutionEvent;
export type HookExecutionEventType = NonNullable<HookExecutionEvent['hook_event_type']>;
export type LLMCompletionLogEvent = AgentServerLlmCompletionLogEvent;
export type MessageEvent = AgentServerMessageEvent;
export type MessageToolCall = AgentServerMessageToolCall;
/** Local conversations expose tool observations as opaque JSON values. */
export type ObservationEvent = Omit<AgentServerObservationEvent, 'observation'> & {
  observation: unknown;
};
export type PauseEvent = AgentServerPauseEvent;
export type SecurityRisk = AgentServerSecurityRisk;
export type StreamingDeltaEvent = AgentServerStreamingDeltaEvent;
export type SystemPromptEvent = AgentServerSystemPromptEvent;
export type TokenEvent = AgentServerTokenEvent;
export type ToolDefinition = AgentServerToolDefinition;
export type UserRejectObservation = AgentServerUserRejectObservation;

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
export interface BaseEvent {
  /** Unique event identifier */
  id?: EventID;
  /** Event type/kind discriminator */
  kind: string;
  /** ISO timestamp when event was created */
  timestamp?: string;
  /** Source of the event */
  source?: EventSource;
  parent_id?: EventID | null;
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
 * Union type of all conversation events
 */
export type ConversationEvent =
  | AgentServerEvent
  | ConfirmationRequestEvent
  | ConfirmationResponseEvent
  | StuckDetectionEvent
  | FinishEvent
  | ThinkEvent;

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
