/**
 * Base types and interfaces for the OpenHands Agent Server TypeScript client
 */

export type ConversationID = string;

/**
 * Base event interface matching the agent-server wire format.
 * For rich, typed events use the specific types from events/types.ts
 * (e.g., ActionEvent, ObservationEvent, ConversationEvent).
 */
export interface Event {
  id: string;
  kind: string;
  timestamp: string;
  source?: 'agent' | 'user' | 'environment' | 'system' | 'hook';
}

export interface Message {
  role: 'user' | 'assistant' | 'system';
  content: MessageContent[];
}

export interface MessageContent {
  type: 'text' | 'image';
  text?: string;
  image_urls?: string[];
}

export interface TextContent extends MessageContent {
  type: 'text';
  text: string;
}

export interface ImageContent extends MessageContent {
  type: 'image';
  image_urls: string[];
}

export interface AgentContext {
  skills?: unknown[];
  system_message_suffix?: string | null;
  user_message_suffix?: string | null;
  [key: string]: unknown;
}

export interface AgentBase {
  kind: string;
  llm: LLM;
  agent_context?: AgentContext | null;
  name?: string;
  [key: string]: unknown;
}

// Alias for user-facing API
export type Agent = AgentBase;

export interface LLM {
  model: string;
  api_key?: string;
  base_url?: string;
  [key: string]: unknown;
}

export interface ServerInfo {
  uptime: number;
  idle_time: number;
  title?: string;
  version: string;
  sdk_version?: string;
  tools_version?: string;
  workspace_version?: string;
  build_git_sha?: string;
  build_git_date?: string;
  build_semver?: string;
  [key: string]: unknown;
}

export interface Success {
  success: boolean;
  message?: string;
}

export interface EventPage {
  items: Event[];
  next_page_id?: string;
  total_count?: number;
}

export enum EventSortOrder {
  TIMESTAMP = 'TIMESTAMP',
  TIMESTAMP_DESC = 'TIMESTAMP_DESC',
}

// eslint-disable-next-line @typescript-eslint/no-namespace
export namespace EventSortOrder {
  /** @deprecated Use TIMESTAMP_DESC instead. */
  export const REVERSE_TIMESTAMP: EventSortOrder = 'TIMESTAMP_DESC' as EventSortOrder;
}

/**
 * Enum representing the current execution state of the conversation.
 * Note: This was renamed from AgentExecutionStatus to ConversationExecutionStatus
 * in the agent-server API.
 */
export enum ConversationExecutionStatus {
  IDLE = 'idle',
  RUNNING = 'running',
  PAUSED = 'paused',
  WAITING_FOR_CONFIRMATION = 'waiting_for_confirmation',
  FINISHED = 'finished',
  ERROR = 'error',
  STUCK = 'stuck',
  DELETING = 'deleting',
}

/**
 * @deprecated Use ConversationExecutionStatus instead. This alias is kept for backward compatibility.
 */
export const AgentExecutionStatus = ConversationExecutionStatus;
export type AgentExecutionStatus = ConversationExecutionStatus;

export interface ConversationStats {
  total_events: number;
  message_events: number;
  action_events: number;
  observation_events: number;
  [key: string]: unknown;
}

export interface ConfirmationPolicyBase {
  type: string;
  [key: string]: unknown;
}

export interface NeverConfirm extends ConfirmationPolicyBase {
  type: 'never';
}

export interface AlwaysConfirm extends ConfirmationPolicyBase {
  type: 'always';
}

export type ConversationCallbackType = (event: Event) => void;

export type SecretValue = string | (() => string);
