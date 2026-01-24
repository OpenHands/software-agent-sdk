/**
 * Rich event types for conversations
 *
 * These event types mirror the Python SDK's event system, providing
 * structured events for all conversation activities.
 */

import { Message, MessageContent } from '../types/base';

/**
 * Event ID type - unique identifier for events
 */
export type EventID = string;

/**
 * Source of an event
 */
export type EventSource = 'agent' | 'user' | 'environment' | 'system';

/**
 * Base interface for all conversation events
 */
export interface BaseEvent {
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
 * Agent error event - error during agent execution
 */
export interface AgentErrorEvent extends BaseEvent {
  kind: 'AgentErrorEvent';
  /** The tool that caused the error */
  tool_name: string;
  /** Tool call ID for correlation */
  tool_call_id: string;
  /** Error message or details */
  error: string;
  /** ID of the action that caused the error */
  action_id: string;
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
 * Condensation summary event - result of conversation condensation
 */
export interface CondensationSummaryEvent extends BaseEvent {
  kind: 'CondensationSummaryEvent';
  /** Summary of condensed content */
  summary: string;
  /** Number of events condensed */
  events_condensed: number;
  /** Token count before condensation */
  tokens_before?: number;
  /** Token count after condensation */
  tokens_after?: number;
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
 * User reject observation - user rejected a pending action
 */
export interface UserRejectObservation extends BaseEvent {
  kind: 'UserRejectObservation';
  /** ID of the rejected action */
  action_id: string;
  /** Reason for rejection */
  reason: string;
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
 * Token event - streaming token from LLM
 */
export interface TokenEvent extends BaseEvent {
  kind: 'TokenEvent';
  /** The token content */
  token: string;
  /** Token index in the current response */
  index?: number;
  /** Whether this is the final token */
  is_final?: boolean;
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
  | MessageEvent
  | ActionEvent
  | ObservationEvent
  | AgentErrorEvent
  | SystemPromptEvent
  | PauseEvent
  | CondensationRequestEvent
  | CondensationSummaryEvent
  | ConversationStateUpdateEvent
  | UserRejectObservation
  | ConfirmationRequestEvent
  | ConfirmationResponseEvent
  | TokenEvent
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
 * Generate a unique event ID
 */
export function generateEventId(): EventID {
  return `evt_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
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
