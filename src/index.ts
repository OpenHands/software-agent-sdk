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
export type { EventSearchOptions, RemoteEventsListOptions } from './events/remote-events-list';

// Stuck Detection
export { StuckDetector, DEFAULT_STUCK_THRESHOLDS } from './conversation/stuck-detector';
export type { StuckDetectionThresholds, StuckDetectionResult } from './conversation/stuck-detector';

// Secret Registry
export {
  SecretRegistry,
  StaticSecretSource,
  CallableSecretSource,
} from './conversation/secret-registry';
export type { SecretSource, SecretSourceKind } from './conversation/secret-registry';

// Security (Confirmation Policy & Security Analyzer)
export {
  NeverConfirm,
  AlwaysConfirm,
  RiskBasedConfirm,
  ToolBasedConfirm,
  CompositeConfirm,
  createConfirmationPolicy,
} from './security/confirmation-policy';
export type {
  RiskLevel,
  SecurityAnalysisResult,
  ConfirmationPolicy,
} from './security/confirmation-policy';

export {
  PatternBasedAnalyzer,
  AllowlistAnalyzer,
  NoOpAnalyzer,
  CompositeAnalyzer,
  createSecurityAnalyzer,
} from './security/security-analyzer';
export type { SecurityAnalyzer } from './security/security-analyzer';

// Rich Event Types
export {
  generateEventId,
  createBaseEvent,
  isMessageEvent,
  isActionEvent,
  isObservationEvent,
  isAgentErrorEvent,
  isObservationLike,
  isConversationErrorEvent,
  isCondensationEvent,
  isHookExecutionEvent,
} from './events/types';
export type {
  EventID,
  EventSource,
  BaseEvent,
  MessageEvent,
  ActionEvent,
  ObservationEvent,
  AgentErrorEvent,
  SystemPromptEvent,
  PauseEvent,
  CondensationRequestEvent,
  CondensationSummaryEvent,
  CondensationEvent,
  ConversationStateUpdateEvent,
  ConversationErrorEvent,
  LLMCompletionLogEvent,
  UserRejectObservation,
  ConfirmationRequestEvent,
  ConfirmationResponseEvent,
  TokenEvent,
  StuckDetectionEvent,
  FinishEvent,
  ThinkEvent,
  HookExecutionEvent,
  HookExecutionEventType,
  ConversationEvent,
} from './events/types';

// Hooks
export {
  HookEventType,
  HookType,
  HookDecision,
  hookResultShouldContinue,
  createSuccessResult,
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
} from './hooks';
export type { HookEvent, HookResult, HookDefinition, HookMatcher, HookConfig } from './hooks';

// Agent classes
export { Agent } from './agent/agent';

// LLM classes and factory functions
export { LLM, OpenRouterLLM, createLLM, createOpenRouterLLM } from './llm';

// Prompts
export {
  DEFAULT_SYSTEM_PROMPT,
  MINIMAL_SYSTEM_PROMPT,
  TOOL_DESCRIPTIONS,
  generateSystemPrompt,
} from './prompts';

// WebSocket client for real-time events
export { WebSocketCallbackClient } from './events/websocket-client';
export type { ErrorCallbackType } from './events/websocket-client';
export { BashWebSocketClient } from './events/bash-websocket-client';
export type { BashWebSocketClientOptions } from './events/bash-websocket-client';

// HTTP client
export { HttpClient, HttpError } from './client/http-client';
export { CloudProxyClient } from './client/cloud-proxy-client';
export { HooksClient } from './client/hooks-client';
export { MCPClient } from './client/mcp-client';
export { WorkspacesClient } from './client/workspaces-client';
export {
  AGENT_SERVER_VERSION_ERROR_CODE,
  AgentServerFeatureRequirements,
  AgentServerVersionError,
  assertAgentServerSupports,
  clearAgentServerInfoCache,
  compareAgentServerVersions,
  getCachedAgentServerInfo,
  isAgentServerVersionError,
} from './client/agent-server-compatibility';

// Types and interfaces
export type {
  ConversationID,
  Event,
  Message,
  MessageContent,
  TextContent,
  ImageContent,
  AgentBase,
  AgentContext,
  LLM as LLMConfig,
  ServerInfo,
  Success,
  EventPage,
  ConversationCallbackType,
  SecretValue,
  ConversationStats,
  ConfirmationPolicyBase,
} from './types/base';

export type { AgentOptions } from './agent/agent';

export { EventSortOrder, AgentExecutionStatus, ConversationExecutionStatus } from './types/base';
export { ConversationSortOrder } from './models/conversation';

// Workspace models
export type {
  CommandResult,
  FileOperationResult,
  FileDownloadResult,
  GitChange,
  GitDiff,
  ExecuteBashRequest,
  BashEventBase,
  BashCommand,
  BashOutput,
  BashError,
  BashEvent,
  BashEventPage,
  BashEventSearchOptions,
  ClearBashEventsResponse,
} from './models/workspace';

// Workspace base types and interface
export type {
  IWorkspace,
  BaseWorkspaceOptions,
  GitQueryOptions,
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

// ACP provider registry (mirrors openhands-sdk; see scripts/validate-acp-providers.mjs)
export { ACP_PROVIDERS, ACP_SETTINGS_KEYS, getAcpProvider } from './models/acp';
export type { ACPModelOption, ACPProviderInfo, ACPProviderKey } from './models/acp';

// Agent profile types (mirrors openhands-sdk agent_profile.py + resolver.py)
export type {
  AgentKind,
  ACPServerKind,
  ProfileVerificationSettings,
  OpenHandsAgentProfile,
  ACPAgentProfile,
  AgentProfile,
  AgentProfileSaveInput,
  AgentProfileSummary,
  AgentProfileDiagnostics,
  LaunchedProfile,
} from './models/agent-profile';

// Agent profiles client
export { AgentProfilesClient } from './client/agent-profiles-client';
export type {
  AgentProfilesClientOptions,
  GetAgentProfileOptions,
  AgentProfileListResponse,
  AgentProfileDetailResponse,
  AgentProfileMutationResponse,
  ActivateAgentProfileResponse,
} from './client/agent-profiles-client';

// deriveSwitchPlan — pure profile-switch decision helper
export { deriveSwitchPlan } from './profiles/derive-switch-plan';
export type { SwitchPlan } from './profiles/derive-switch-plan';

// Conversation models
export type {
  ConversationInfo,
  ACPAgentConfig,
  ACPConversationInfo,
  SendMessageRequest,
  ConfirmationResponseRequest,
  CreateConversationRequest,
  CreateACPConversationRequest,
  UpdateConversationRequest,
  UpdateSecretsRequest,
  StaticSecret,
  LookupSecret,
  SecretObject,
  ConversationSearchRequest,
  ConversationSearchResponse,
  ACPConversationSearchResponse,
  AskAgentRequest,
  AskAgentResponse,
  StartGoalRequest,
  SetSecurityAnalyzerRequest,
  SetConfirmationPolicyRequest,
  ConversationEventSearchOptions,
  ConversationEventCountOptions,
  ForkConversationRequest,
  AgentResponseResult,
  ConversationEvent as ConversationApiEvent,
  ConversationEventPage,
} from './models/conversation';

// Client options
export type { HttpClientOptions, RequestOptions, HttpResponse } from './client/http-client';
export type { CloudProxyClientOptions } from './client/cloud-proxy-client';
export type { HooksClientOptions } from './client/hooks-client';
export type { MCPClientOptions } from './client/mcp-client';
export type {
  DeleteWorkspaceResponse,
  WorkspacesClientOptions,
  WorkspacesListResponse,
  WorkspaceItem,
  WorkspaceParentItem,
} from './client/workspaces-client';
export type { AgentServerFeatureRequirement } from './client/agent-server-compatibility';

export type {
  AliveStatus,
  HealthStatus,
  ReadyStatus,
  ProvidersResponse,
  ModelsResponse,
  VerifiedModelsResponse,
  SettingsSchema,
  ExposedUrl,
  OrgConfig,
  SandboxConfig,
  SkillsRequest,
  SkillInfo,
  SkillsResponse,
  SyncResponse,
  InstallSkillRequest,
  InstalledSkillInfo,
  InstalledSkillSummary,
  InstalledSkillsResponse,
  ToggleSkillResponse,
  SkillActionResponse,
  RefreshSkillResponse,
  MarketplaceSkill,
  MarketplaceResponse,
  MarketplacePlugin,
  MarketplaceCatalogResponse,
  PluginsRequest,
  PluginInfo,
  PluginsResponse,
  InstallPluginRequest,
  InstalledPluginInfo,
  InstalledPluginsResponse,
  TogglePluginResponse,
  PluginActionResponse,
  RefreshPluginResponse,
  DesktopUrlResponse,
  VSCodeUrlResponse,
  VSCodeStatusResponse,
  ProfileInfo,
  ProfileListResponse,
  ProfileDetailResponse,
  ProfileMutationResponse,
  ActivateProfileResponse,
  SaveProfileRequest,
  RenameProfileRequest,
  MetaProfileClass,
  MetaProfile,
  MetaProfileInfo,
  MetaProfileListResponse,
  MetaProfileDetailResponse,
  MetaProfileMutationResponse,
  ActivateMetaProfileResponse,
  ExposeSecretsMode,
  SettingsValue,
  SettingsApiResponse,
  SettingsUpdateRequest,
  SecretInfo,
  SecretsListResponse,
  UpsertSecretRequest,
  UpsertSecretResponse,
  DeleteSecretResponse,
  SecretValueResponse,
  FileSubdirectoryEntry,
  FileSubdirectoryPage,
  FileHomeResponse,
  FileSearchSubdirsOptions,
  CloudProxyRequest,
  CloudProxyResponse,
  HooksRequest,
  HooksResponse,
  StdioMCPServerSpec,
  RemoteMCPServerType,
  RemoteMCPServerSpec,
  MCPServerSpec,
  MCPTestRequest,
  MCPTestSuccess,
  MCPTestFailureKind,
  MCPTestFailure,
  MCPTestResponse,
  SharedConversation,
  EventPage as ApiEventPage,
} from './models/api';

export type { WebSocketClientOptions } from './events/websocket-client';

export type { RemoteWorkspaceOptions } from './workspace/remote-workspace';

export type { LocalWorkspaceOptions } from './workspace/local-workspace';

export type { WorkspaceOptions, CreateWorkspaceOptions } from './workspace/workspace';

export type { RemoteConversationOptions } from './conversation/remote-conversation';

export type {
  LocalConversationOptions,
  ToolExecutor,
  ConversationTokenCallback,
} from './conversation/local-conversation';

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
  TokenCallbackType,
  TokenStreamEvent,
} from './llm';
export type { OpenRouterLLMOptions, LLMOptions, CreateLLMOptions } from './llm';

// Prompt types
export type { SystemPromptOptions } from './prompts';

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
import { BashWebSocketClient } from './events/bash-websocket-client';
import { HttpClient, HttpError } from './client/http-client';
import { CloudProxyClient } from './client/cloud-proxy-client';
import { HooksClient } from './client/hooks-client';
import { MCPClient } from './client/mcp-client';
import { WorkspacesClient } from './client/workspaces-client';
import {
  AGENT_SERVER_VERSION_ERROR_CODE,
  AgentServerFeatureRequirements,
  AgentServerVersionError,
  assertAgentServerSupports,
  clearAgentServerInfoCache,
  compareAgentServerVersions,
  getCachedAgentServerInfo,
  isAgentServerVersionError,
} from './client/agent-server-compatibility';
import { EventSortOrder, AgentExecutionStatus, ConversationExecutionStatus } from './types/base';
import { ConversationSortOrder } from './models/conversation';
import { Agent } from './agent/agent';
import { LLM, OpenRouterLLM, createLLM, createOpenRouterLLM } from './llm';
import {
  HookEventType,
  HookType,
  HookDecision,
  hookResultShouldContinue,
  createSuccessResult,
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
} from './hooks';

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
  BashWebSocketClient,
  HttpClient,
  HttpError,
  CloudProxyClient,
  HooksClient,
  MCPClient,
  WorkspacesClient,
  AGENT_SERVER_VERSION_ERROR_CODE,
  AgentServerFeatureRequirements,
  AgentServerVersionError,
  assertAgentServerSupports,
  clearAgentServerInfoCache,
  compareAgentServerVersions,
  getCachedAgentServerInfo,
  isAgentServerVersionError,
  EventSortOrder,
  ConversationSortOrder,
  AgentExecutionStatus,
  ConversationExecutionStatus,
  Agent,
  LLM,
  OpenRouterLLM,
  createLLM,
  createOpenRouterLLM,
  HookEventType,
  HookType,
  HookDecision,
  hookResultShouldContinue,
  createSuccessResult,
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
};
