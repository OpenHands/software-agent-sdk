/**
 * OpenHands Agent Server TypeScript Client
 *
 * A TypeScript client library for the OpenHands Agent Server API that mirrors
 * the structure and functionality of the Python SDK.
 */

// Main conversation and workspace classes
export { RemoteConversation } from './conversation/remote-conversation';
export { Conversation } from './conversation/conversation';
export { ConversationManager } from './conversation/conversation-manager';
export { RemoteWorkspace } from './workspace/remote-workspace';
export { Workspace } from './workspace/workspace';
export { RemoteState } from './conversation/remote-state';
export { RemoteEventsList } from './events/remote-events-list';

// Agent classes
export { Agent } from './agent/agent';

// WebSocket client for real-time events
export { WebSocketCallbackClient } from './events/websocket-client';

// HTTP client
export { HttpClient, HttpError } from './client/http-client';

// Types and interfaces
export type {
  ConversationID,
  Event,
  MessageEvent,
  ActionEvent,
  ObservationEvent,
  AgentErrorEvent,
  SystemPromptEvent,
  PauseEvent,
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
} from './types/base';

export type { AgentOptions } from './agent/agent';

export { EventSortOrder, AgentExecutionStatus } from './types/base';

// Workspace models
export type { CommandResult, FileOperationResult, GitChange, GitDiff } from './models/workspace';

// Conversation models
export type {
  ConversationInfo,
  SendMessageRequest,
  ConfirmationResponseRequest,
  CreateConversationRequest,
  GenerateTitleRequest,
  GenerateTitleResponse,
  UpdateSecretsRequest,
  ConversationSearchRequest,
  ConversationSearchResponse,
} from './models/conversation';

// Client options
export type { HttpClientOptions, RequestOptions, HttpResponse } from './client/http-client';

export type { WebSocketClientOptions } from './events/websocket-client';

export type { RemoteWorkspaceOptions } from './workspace/remote-workspace';

export type { RemoteConversationOptions } from './conversation/remote-conversation';

export type { ConversationManagerOptions } from './conversation/conversation-manager';

// Re-import for default export
import { RemoteConversation } from './conversation/remote-conversation';
import { Conversation } from './conversation/conversation';
import { ConversationManager } from './conversation/conversation-manager';
import { RemoteWorkspace } from './workspace/remote-workspace';
import { Workspace } from './workspace/workspace';
import { RemoteState } from './conversation/remote-state';
import { RemoteEventsList } from './events/remote-events-list';
import { WebSocketCallbackClient } from './events/websocket-client';
import { HttpClient, HttpError } from './client/http-client';
import { EventSortOrder, AgentExecutionStatus } from './types/base';
import { Agent } from './agent/agent';

// Default export for convenience
export default {
  RemoteConversation,
  Conversation,
  ConversationManager,
  RemoteWorkspace,
  Workspace,
  RemoteState,
  RemoteEventsList,
  WebSocketCallbackClient,
  HttpClient,
  HttpError,
  EventSortOrder,
  AgentExecutionStatus,
  Agent,
};
