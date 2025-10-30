/**
 * OpenHands Agent Server TypeScript Client
 * 
 * A TypeScript client library for the OpenHands Agent Server API that mirrors
 * the structure and functionality of the Python SDK.
 */

// Main conversation and workspace classes
export { RemoteConversation } from './conversation/remote-conversation.js';
export { RemoteWorkspace } from './workspace/remote-workspace.js';
export { RemoteState } from './conversation/remote-state.js';
export { RemoteEventsList } from './events/remote-events-list.js';

// WebSocket client for real-time events
export { WebSocketCallbackClient } from './events/websocket-client.js';

// HTTP client
export { HttpClient, HttpError } from './client/http-client.js';

// Types and interfaces
export type {
  ConversationID,
  Event,
  Message,
  MessageContent,
  TextContent,
  ImageContent,
  AgentBase,
  LLM,
  ServerInfo,
  Success,
  EventPage,
  ConversationCallbackType,
  SecretValue,
  ConversationStats,
  ConfirmationPolicyBase,
  NeverConfirm,
  AlwaysConfirm,
} from './types/base.js';

export {
  EventSortOrder,
  AgentExecutionStatus,
} from './types/base.js';

// Workspace models
export type {
  CommandResult,
  FileOperationResult,
  GitChange,
  GitDiff,
} from './models/workspace.js';

// Conversation models
export type {
  ConversationInfo,
  SendMessageRequest,
  ConfirmationResponseRequest,
  CreateConversationRequest,
  GenerateTitleRequest,
  GenerateTitleResponse,
  UpdateSecretsRequest,
} from './models/conversation.js';

// Client options
export type {
  HttpClientOptions,
  RequestOptions,
  HttpResponse,
} from './client/http-client.js';

export type {
  WebSocketClientOptions,
} from './events/websocket-client.js';

export type {
  RemoteWorkspaceOptions,
} from './workspace/remote-workspace.js';

export type {
  RemoteConversationOptions,
} from './conversation/remote-conversation.js';

// Re-import for default export
import { RemoteConversation } from './conversation/remote-conversation.js';
import { RemoteWorkspace } from './workspace/remote-workspace.js';
import { RemoteState } from './conversation/remote-state.js';
import { RemoteEventsList } from './events/remote-events-list.js';
import { WebSocketCallbackClient } from './events/websocket-client.js';
import { HttpClient, HttpError } from './client/http-client.js';
import { EventSortOrder, AgentExecutionStatus } from './types/base.js';

// Default export for convenience
export default {
  RemoteConversation,
  RemoteWorkspace,
  RemoteState,
  RemoteEventsList,
  WebSocketCallbackClient,
  HttpClient,
  HttpError,
  EventSortOrder,
  AgentExecutionStatus,
};