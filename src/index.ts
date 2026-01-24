/**
 * OpenHands Agent Server TypeScript Client
 *
 * A TypeScript client library for the OpenHands Agent Server API that mirrors
 * the structure and functionality of the Python SDK.
 */

// Main conversation and workspace classes
export { RemoteConversation } from './conversation/remote-conversation';
export { LocalConversation } from './conversation/local-conversation';
export {
  Conversation,
  createConversation,
  createConversationAuto,
} from './conversation/conversation';
export { ConversationManager } from './conversation/conversation-manager';
export { RemoteWorkspace } from './workspace/remote-workspace';
export { LocalWorkspace } from './workspace/local-workspace';
export { Workspace, createWorkspace, createWorkspaceAuto } from './workspace/workspace';
export { RemoteState } from './conversation/remote-state';
export { RemoteEventsList } from './events/remote-events-list';

// Agent classes
export { Agent } from './agent/agent';

// LLM classes and factory functions
export { LLM, OpenRouterLLM, createLLM, createOpenRouterLLM } from './llm';

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
  LLM as LLMConfig,
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
export type {
  CommandResult,
  FileOperationResult,
  FileDownloadResult,
  GitChange,
  GitDiff,
} from './models/workspace';

// Workspace base types and interface
export type {
  IWorkspace,
  BaseWorkspaceOptions,
  WorkspaceType,
} from './workspace/base';

// Conversation base types and interface
export type {
  IConversation,
  IConversationState,
  IEventsList,
  BaseConversationOptions,
  ConversationType,
} from './conversation/base';

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

export type { LocalWorkspaceOptions } from './workspace/local-workspace';

export type { WorkspaceOptions, CreateWorkspaceOptions } from './workspace/workspace';

export type { RemoteConversationOptions } from './conversation/remote-conversation';

export type { LocalConversationOptions } from './conversation/local-conversation';

export type { ConversationOptions, CreateConversationOptions } from './conversation/conversation';

export type { ConversationManagerOptions } from './conversation/conversation-manager';

// LLM types and interfaces
export type {
  ILLM,
  BaseLLMOptions,
  LLMProviderType,
  MessageRole,
  ContentPart,
  ChatMessage,
  Tool,
  ToolCall,
  ChatCompletionOptions,
  ChatCompletionChoice,
  TokenUsage,
  ChatCompletionResponse,
  ChatCompletionChunk,
} from './llm';
export type { OpenRouterLLMOptions, LLMOptions, CreateLLMOptions } from './llm';

// Re-import for default export
import { RemoteConversation } from './conversation/remote-conversation';
import { LocalConversation } from './conversation/local-conversation';
import {
  Conversation,
  createConversation,
  createConversationAuto,
} from './conversation/conversation';
import { ConversationManager } from './conversation/conversation-manager';
import { RemoteWorkspace } from './workspace/remote-workspace';
import { LocalWorkspace } from './workspace/local-workspace';
import { Workspace, createWorkspace, createWorkspaceAuto } from './workspace/workspace';
import { RemoteState } from './conversation/remote-state';
import { RemoteEventsList } from './events/remote-events-list';
import { WebSocketCallbackClient } from './events/websocket-client';
import { HttpClient, HttpError } from './client/http-client';
import { EventSortOrder, AgentExecutionStatus } from './types/base';
import { Agent } from './agent/agent';
import { LLM, OpenRouterLLM, createLLM, createOpenRouterLLM } from './llm';

// Default export for convenience
export default {
  RemoteConversation,
  LocalConversation,
  Conversation,
  createConversation,
  createConversationAuto,
  ConversationManager,
  RemoteWorkspace,
  LocalWorkspace,
  Workspace,
  createWorkspace,
  createWorkspaceAuto,
  RemoteState,
  RemoteEventsList,
  WebSocketCallbackClient,
  HttpClient,
  HttpError,
  EventSortOrder,
  AgentExecutionStatus,
  Agent,
  LLM,
  OpenRouterLLM,
  createLLM,
  createOpenRouterLLM,
};
