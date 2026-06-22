/**
 * Models for auxiliary Agent Server APIs.
 */

import type { HookConfig } from '../hooks';
import type { LLM } from '../types/base';

export interface AliveStatus {
  status: string;
}

export interface HealthStatus {
  status: string;
}

export interface ReadyStatus {
  status: string;
  message?: string;
}

export interface ProvidersResponse {
  providers: string[];
}

export interface ModelsResponse {
  models: string[];
}

export interface VerifiedModelsResponse {
  models: Record<string, string[]>;
}

export interface LLMSubscriptionStatusResponse {
  vendor: string;
  connected: boolean;
  account_email: string | null;
  expires_at: number | null;
}

export interface LLMSubscriptionDeviceStartResponse {
  device_code: string;
  user_code: string;
  verification_uri: string;
  verification_uri_complete: string | null;
  expires_at: number;
  interval_seconds: number;
}

export interface LLMSubscriptionDevicePollRequest {
  device_code: string;
}

export interface LLMSubscriptionModelsResponse {
  vendor: string;
  models: string[];
}

export interface SettingsSchema {
  model_name: string;
  sections: Array<Record<string, unknown>>;
}

export interface ExposedUrl {
  name: string;
  url: string;
  port: number;
}

export interface OrgConfig {
  repository: string;
  provider: string;
  org_repo_url: string;
  org_name: string;
}

export interface SandboxConfig {
  exposed_urls: ExposedUrl[];
}

export interface SkillsRequest {
  load_public?: boolean;
  load_user?: boolean;
  load_project?: boolean;
  load_org?: boolean;
  marketplace_path?: string | null;
  project_dir?: string | null;
  org_config?: OrgConfig | null;
  sandbox_config?: SandboxConfig | null;
}

export interface SkillInfo {
  name: string;
  type: 'repo' | 'knowledge' | 'agentskills';
  content: string;
  triggers: string[];
  source?: string | null;
  description?: string | null;
  is_agentskills_format?: boolean;
}

export interface SkillsResponse {
  skills: SkillInfo[];
  sources: Record<string, number>;
}

export interface SyncResponse {
  status: 'success' | 'error';
  message: string;
}

export interface InstallSkillRequest {
  source: string;
  force?: boolean;
  ref?: string | null;
  repo_path?: string | null;
}

export interface InstalledSkillInfo {
  name: string;
  version?: string | null;
  description?: string | null;
  enabled: boolean;
  source?: string | null;
  installed_at?: string | null;
  install_path?: string | null;
}

export interface InstalledSkillSummary {
  name: string;
  version?: string | null;
  enabled: boolean;
}

export interface InstalledSkillsResponse {
  skills: InstalledSkillSummary[];
}

export interface ToggleSkillResponse {
  name: string;
  enabled: boolean;
}

export interface SkillActionResponse {
  message: string;
}

export interface RefreshSkillResponse {
  message: string;
  skill: InstalledSkillSummary;
}

export interface MarketplaceSkill {
  name: string;
  description: string;
  source: string;
  installed: boolean;
}

export interface MarketplaceResponse {
  skills: MarketplaceSkill[];
}

export interface DesktopUrlResponse {
  url: string | null;
}

export interface VSCodeUrlResponse {
  url: string | null;
}

export interface VSCodeStatusResponse {
  running: boolean;
  enabled: boolean;
  message?: string;
}

export interface ProfileInfo {
  name: string;
  model: string | null;
  base_url: string | null;
  api_key_set: boolean;
}

export interface ProfileListResponse {
  profiles: ProfileInfo[];
  active_profile: string | null;
}

export interface ProfileDetailResponse {
  name: string;
  config: Record<string, unknown>;
  api_key_set: boolean;
}

export interface ProfileMutationResponse {
  name: string;
  message: string;
}

export interface ActivateProfileResponse {
  name: string;
  message: string;
  llm_applied: boolean;
}

export interface SaveProfileRequest {
  llm: LLM;
  include_secrets?: boolean;
}

export interface RenameProfileRequest {
  new_name: string;
}

/**
 * Meta-profiles: declarative model-routing configurations consumed by the
 * ``classify_and_switch_llm`` tool (agent-server ``/api/meta-profiles``).
 *
 * Every model reference (``classifier_model``, ``default_model`` and each
 * class's ``model``) is the name of a saved LLM profile, not a raw model
 * string.
 */
export interface MetaProfileClass {
  description: string;
  /** Name of the saved LLM profile to switch to for this class. */
  model: string;
}

export interface MetaProfile {
  /** Name of the saved LLM profile used to classify the task. */
  classifier_model: string;
  /** Name of the saved LLM profile to use when no class matches. */
  default_model: string;
  classes: MetaProfileClass[];
}

export interface MetaProfileInfo {
  name: string;
  classifier_model: string | null;
  default_model: string | null;
  num_classes: number;
}

export interface MetaProfileListResponse {
  meta_profiles: MetaProfileInfo[];
  active_meta_profile: string | null;
}

export interface MetaProfileDetailResponse {
  name: string;
  config: MetaProfile;
}

export interface MetaProfileMutationResponse {
  name: string;
  message: string;
}

export interface ActivateMetaProfileResponse {
  name: string;
  message: string;
}

export type ExposeSecretsMode = 'encrypted' | 'plaintext';

export type SettingsValue = unknown;

export interface SettingsApiResponse {
  agent_settings: Record<string, SettingsValue>;
  conversation_settings: Record<string, SettingsValue>;
  llm_api_key_is_set: boolean;
  [key: string]: unknown;
}

export interface SettingsUpdateRequest {
  agent_settings_diff?: Record<string, SettingsValue>;
  conversation_settings_diff?: Record<string, SettingsValue>;
  [key: string]: unknown;
}

export interface SecretInfo {
  name: string;
  description?: string;
}

export interface SecretsListResponse {
  secrets: SecretInfo[];
}

export interface UpsertSecretRequest {
  name: string;
  value: string;
  description?: string;
}

export interface UpsertSecretResponse {
  name: string;
  description?: string;
}

export interface DeleteSecretResponse {
  deleted: boolean;
}

export type SecretValueResponse = string;

export interface FileSubdirectoryEntry {
  name: string;
  path: string;
}

export interface FileSubdirectoryPage {
  items: FileSubdirectoryEntry[];
  next_page_id: string | null;
}

export interface FileBrowserEntry {
  label: string;
  path: string;
}

export interface FileHomeResponse {
  home: string;
  favorites?: FileBrowserEntry[];
  locations?: FileBrowserEntry[];
}

export interface FileHomeOptions {
  /** Include hidden top-level directories in the response's `favorites`. */
  includeHidden?: boolean;
}

export interface FileSearchSubdirsOptions {
  pageId?: string | null;
  limit?: number;
  /** Include hidden subdirectories (names starting with '.'). */
  includeHidden?: boolean;
}

export interface CloudProxyRequest {
  host: string;
  path: string;
  method?: string;
  headers?: Record<string, string>;
  body?: unknown;
  timeout_seconds?: number;
}

export type CloudProxyResponse = unknown;

export interface HooksRequest {
  project_dir?: string | null;
}

export interface HooksResponse {
  hook_config?: HookConfig | null;
}

export interface StdioMCPServerSpec {
  type: 'stdio';
  command: string;
  args?: string[];
  env?: Record<string, string>;
  cwd?: string | null;
}

export type RemoteMCPServerType = 'http' | 'shttp' | 'streamable-http' | 'sse';

export interface RemoteMCPServerSpec {
  type: RemoteMCPServerType;
  url: string;
  headers?: Record<string, string>;
  api_key?: string | null;
}

export type MCPServerSpec = StdioMCPServerSpec | RemoteMCPServerSpec;

export interface MCPTestRequest {
  server: MCPServerSpec;
  name?: string;
  timeout?: number;
}

export interface MCPTestSuccess {
  ok: true;
  tools: string[];
}

export type MCPTestFailureKind = 'timeout' | 'connection' | 'unknown';

export interface MCPTestFailure {
  ok: false;
  error: string;
  error_kind: MCPTestFailureKind;
}

export type MCPTestResponse = MCPTestSuccess | MCPTestFailure;

export interface SecuritySettings {
  RISK_SEVERITY: number;
  [key: string]: unknown;
}

export type SecurityTraceResponse = unknown;

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
}

export interface CreateApiKeyResponse {
  id: string;
  name: string;
  key: string;
  prefix: string;
  created_at: string;
}

export interface AcceptTosResponse {
  redirect_url?: string;
}

export interface SharedConversation {
  id: string;
  created_by_user_id: string | null;
  selected_repository: string | null;
  selected_branch: string | null;
  git_provider: string | null;
  title: string | null;
  pr_number: number[];
  llm_model: string | null;
  metrics: unknown | null;
  parent_conversation_id: string | null;
  sub_conversation_ids: string[];
  created_at: string;
  updated_at: string;
  [key: string]: unknown;
}

export interface EventPage<TEvent = unknown> {
  items: TEvent[];
  next_page_id: string | null;
}
