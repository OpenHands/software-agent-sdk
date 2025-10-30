/**
 * Base types and interfaces for the OpenHands Agent Server TypeScript client
 */

export type ConversationID = string;

export interface Event {
  id: string;
  kind: string;
  timestamp: string;
  source?: 'agent' | 'user' | 'environment';
  [key: string]: any;
}

// Specific event types for better type safety
export interface MessageEvent extends Event {
  kind: 'MessageEvent';
  llm_message: Message;
  activated_skills?: string[];
}

export interface ActionEvent extends Event {
  kind: 'ActionEvent';
  action: any; // The action object varies by action type
}

export interface ObservationEvent extends Event {
  kind: 'ObservationEvent';
  tool_name: string;
  tool_call_id: string;
  observation: any;
  action_id: string;
}

export interface AgentErrorEvent extends Event {
  kind: 'AgentErrorEvent';
  tool_name: string;
  tool_call_id: string;
  observation: any;
  action_id: string;
}

export interface SystemPromptEvent extends Event {
  kind: 'SystemPromptEvent';
  system_prompt: TextContent;
  tools: any[];
}

export interface PauseEvent extends Event {
  kind: 'PauseEvent';
}

export interface Message {
  role: 'user' | 'assistant' | 'system';
  content: MessageContent[];
}

export interface MessageContent {
  type: 'text' | 'image';
  text?: string;
  image_url?: string;
}

export interface TextContent extends MessageContent {
  type: 'text';
  text: string;
}

export interface ImageContent extends MessageContent {
  type: 'image';
  image_url: string;
}

export interface AgentBase {
  kind: string;
  llm: LLM;
  // Keep name for backward compatibility
  name?: string;
  [key: string]: any;
}

export interface LLM {
  model: string;
  api_key?: string;
  base_url?: string;
  [key: string]: any;
}

export interface ServerInfo {
  version: string;
  [key: string]: any;
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
  REVERSE_TIMESTAMP = 'REVERSE_TIMESTAMP',
}

export enum AgentExecutionStatus {
  IDLE = 'idle',
  RUNNING = 'running',
  PAUSED = 'paused',
  WAITING_FOR_CONFIRMATION = 'waiting_for_confirmation',
  FINISHED = 'finished',
  ERROR = 'error',
  STUCK = 'stuck',
}

export interface ConversationStats {
  total_events: number;
  message_events: number;
  action_events: number;
  observation_events: number;
  [key: string]: any;
}

export interface ConfirmationPolicyBase {
  type: string;
  [key: string]: any;
}

export interface NeverConfirm extends ConfirmationPolicyBase {
  type: 'never';
}

export interface AlwaysConfirm extends ConfirmationPolicyBase {
  type: 'always';
}

export type ConversationCallbackType = (event: Event) => void;

export type SecretValue = string | (() => string);
