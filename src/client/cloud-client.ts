import { HttpError, type ResponseType } from './http-client';
import {
  OpenHandsClient,
  type OpenHandsClientOptions,
  type OpenHandsRequestOptions,
} from './openhands-client';
import {
  pollForToken,
  startDeviceFlow,
  type DeviceAuthorizationResponse,
  type DeviceFlowRequestOptions,
  type DeviceTokenResponse,
  type PollDeviceTokenOptions,
} from './device-flow-client';
import type {
  ActivateProfileResponse,
  ProfileDetailResponse,
  ProfileListResponse,
  ProfileMutationResponse,
  SaveProfileRequest,
  SettingsSchema,
} from '../models/api';

export interface CloudProxyOptions {
  /** Agent-server or ingress host exposing `/api/cloud-proxy`. */
  host: string;
  /** Optional local session API key for the proxy endpoint. */
  apiKey?: string;
  /** Additional headers for the proxy endpoint itself. */
  headers?: Record<string, string>;
  /** Defaults to `/api/cloud-proxy`. */
  path?: string;
}

export interface CloudClientOptions extends OpenHandsClientOptions {
  /** Locally selected org. Sent as `X-Org-Id` on cloud app-host requests. */
  orgId?: string | null;
  /** Runtime-sandbox requests with `hostOverride` are tunneled through this proxy. */
  proxy?: CloudProxyOptions;
}

export interface CloudRequestOptions extends OpenHandsRequestOptions {
  timeoutSeconds?: number;
}

export interface CloudOrganization {
  id: string;
  name: string;
  is_personal?: boolean;
}

export interface CloudOrganizationsResponse {
  items: CloudOrganization[];
  current_org_id: string | null;
}

export interface CloudOrganizationsResult {
  items: CloudOrganization[];
  currentOrgId: string | null;
}

export interface CloudApiKeyMetadata {
  id: string;
  name: string;
  org_id: string | null;
  user_id: string;
  auth_type: string;
}

export interface CloudOrganizationMe {
  orgId: string;
  userId: string;
  role: string | null;
  permissions?: string[] | null;
}

export type CloudSettingsValue =
  boolean | number | string | null | CloudSettingsValue[] | { [key: string]: CloudSettingsValue };

export interface CloudSettingsResponse {
  llm_model?: string;
  llm_base_url?: string;
  llm_api_key?: string | null;
  llm_api_key_set?: boolean;
  search_api_key_set?: boolean;
  agent?: string;
  confirmation_mode?: boolean;
  security_analyzer?: string | null;
  max_iterations?: number | null;
  enable_default_condenser?: boolean;
  condenser_max_size?: number | null;
  provider_tokens_set?: Partial<Record<string, string | null>>;
  mcp_config?: Record<string, CloudSettingsValue>;
  disabled_skills?: string[];
  agent_settings?: Record<string, CloudSettingsValue> | null;
  conversation_settings?: Record<string, CloudSettingsValue> | null;
  agent_settings_schema?: unknown;
  conversation_settings_schema?: unknown;
  [key: string]: unknown;
}

export interface SaveCloudSettingsRequest {
  agent_settings_diff?: Record<string, CloudSettingsValue>;
  conversation_settings_diff?: Record<string, CloudSettingsValue>;
  app_preferences?: Record<string, unknown>;
}

export interface CloudSecret {
  name: string;
  value?: string;
  description?: string;
}

export type CloudSecretWithoutValue = Omit<CloudSecret, 'value'>;

export interface CloudSkillInfo {
  name: string;
  type: 'repo' | 'knowledge' | 'agentskills';
  source?: string | null;
  description?: string | null;
  triggers?: string[];
  version?: string;
  license?: string | null;
  compatibility?: string | null;
  metadata?: Record<string, string> | null;
  allowed_tools?: string[] | null;
  is_agentskills_format?: boolean;
  disable_model_invocation?: boolean;
  content?: string;
}

export interface CloudGitRepository {
  id: string;
  full_name: string;
  git_provider: string;
  is_public: boolean;
  stargazers_count?: number;
  link_header?: string;
  pushed_at?: string;
  main_branch?: string;
}

export interface CloudGitBranch {
  name: string;
  commit_sha: string;
  protected: boolean;
  last_push_date?: string;
}

export interface CloudPage<T> {
  items: T[];
  next_page_id: string | null;
}

export type CloudRepositoryPage = CloudPage<CloudGitRepository>;
export type CloudBranchPage = CloudPage<CloudGitBranch>;
export type CloudInstallationPage = CloudPage<string>;

export interface CloudSuggestedTask {
  git_provider: string;
  issue_number: number;
  repo: string;
  title: string;
  task_type: string;
}

export interface CloudConversationStartRequest {
  conversation_id?: string | null;
  initial_message?: unknown;
  processors?: unknown[];
  llm_model?: string | null;
  selected_repository?: string | null;
  selected_branch?: string | null;
  git_provider?: string | null;
  suggested_task?: CloudSuggestedTask | null;
  title?: string | null;
  trigger?: string | null;
  pr_number?: number[];
  parent_conversation_id?: string | null;
  agent_type?: 'default' | 'plan';
  sandbox_id?: string | null;
  plugins?: unknown[] | null;
}

export interface CloudConversationStartTask {
  id: string;
  created_by_user_id: string | null;
  status: string;
  detail: string | null;
  app_conversation_id: string | null;
  agent_server_url: string | null;
  request: CloudConversationStartRequest;
  created_at: string;
  updated_at: string;
}

export interface CloudAppConversation {
  id: string;
  created_by_user_id: string | null;
  selected_repository: string | null;
  selected_branch: string | null;
  git_provider: string | null;
  title: string | null;
  trigger: string | null;
  pr_number: number[];
  llm_model: string | null;
  metrics: unknown;
  created_at: string;
  updated_at: string;
  execution_status: string | null;
  sandbox_status?: string | null;
  conversation_url: string | null;
  session_api_key: string | null;
  sandbox_id: string | null;
  workspace?: { working_dir: string | null } | null;
  public?: boolean;
  sub_conversation_ids: string[];
  [key: string]: unknown;
}

export type CloudConversationPage = CloudPage<CloudAppConversation>;

export interface CloudSandboxInfo {
  id: string;
  created_by_user_id: string | null;
  sandbox_spec_id: string;
  status: 'STARTING' | 'RUNNING' | 'PAUSED' | 'ERROR' | 'MISSING';
  session_api_key: string | null;
  exposed_urls: Array<{ name: string; url: string }> | null;
  created_at: string;
}

interface CloudSecretsPage {
  items: CloudSecretWithoutValue[];
  next_page_id: string | null;
}

interface CloudSkillsPage {
  items: CloudSkillInfo[];
  next_page_id: string | null;
}

const SETTINGS_PROFILES_PATH = '/api/v1/settings/profiles';
const DEFAULT_PAGE_LIMIT = 100;

export class CloudClient extends OpenHandsClient {
  readonly kind = 'cloud' as const;
  readonly orgId: string | null;
  readonly proxy?: CloudProxyOptions;

  constructor(options: CloudClientOptions) {
    super(options);
    this.orgId = options.orgId ?? null;
    this.proxy = options.proxy
      ? {
          ...options.proxy,
          host: options.proxy.host.replace(/\/+$/, ''),
          path: options.proxy.path ?? '/api/cloud-proxy',
        }
      : undefined;
  }

  async request<TResponse = unknown>(options: CloudRequestOptions): Promise<TResponse> {
    return options.hostOverride
      ? this.requestThroughProxy<TResponse>(options)
      : this.requestDirect<TResponse>(options);
  }

  startDeviceFlow(options: DeviceFlowRequestOptions = {}): Promise<DeviceAuthorizationResponse> {
    return startDeviceFlow(this.host, options);
  }

  pollForToken(deviceCode: string, options: PollDeviceTokenOptions): Promise<DeviceTokenResponse> {
    return pollForToken(this.host, deviceCode, options);
  }

  async getOrganizations(): Promise<CloudOrganizationsResult> {
    const data = await this.get<CloudOrganizationsResponse>('/api/organizations');
    return {
      items: data?.items ?? [],
      currentOrgId: data?.current_org_id ?? null,
    };
  }

  getCurrentApiKey(): Promise<CloudApiKeyMetadata> {
    return this.get<CloudApiKeyMetadata>('/api/keys/current');
  }

  async getOrganizationMe(orgId: string): Promise<CloudOrganizationMe> {
    const data = await this.get<{
      org_id: string;
      user_id: string;
      role?: string;
      permissions?: string[];
    }>(`/api/organizations/${encodeURIComponent(orgId)}/me`);
    return {
      orgId: data?.org_id ?? orgId,
      userId: data?.user_id ?? '',
      role: data?.role ?? null,
      permissions: Array.isArray(data?.permissions) ? data.permissions : null,
    };
  }

  getSettings(): Promise<CloudSettingsResponse> {
    return this.get<CloudSettingsResponse>('/api/v1/settings');
  }

  async getSettingsWithDerivedFields(): Promise<CloudSettingsResponse> {
    const flat = await this.getSettings();
    return {
      ...flat,
      agent_settings: deriveAgentSettings(flat),
      conversation_settings: deriveConversationSettings(flat),
      llm_api_key_set: !!flat.llm_api_key_set,
      search_api_key_set: !!flat.search_api_key_set,
      provider_tokens_set: flat.provider_tokens_set,
    };
  }

  async saveSettings(diff: SaveCloudSettingsRequest): Promise<void> {
    const body: Record<string, unknown> = {};
    if (diff.agent_settings_diff) {
      const agentDiff = { ...diff.agent_settings_diff };
      if (agentDiff.agent_context === null) {
        delete agentDiff.agent_context;
      }
      if (Object.keys(agentDiff).length > 0) {
        body.agent_settings_diff = agentDiff;
      }
    }
    if (
      diff.conversation_settings_diff &&
      Object.keys(diff.conversation_settings_diff).length > 0
    ) {
      body.conversation_settings_diff = diff.conversation_settings_diff;
    }
    if (diff.app_preferences) {
      for (const [key, value] of Object.entries(diff.app_preferences)) {
        if (value !== undefined) {
          body[key] = value;
        }
      }
    }
    await this.post('/api/v1/settings', body);
  }

  getSettingsSchema(): Promise<SettingsSchema> {
    return this.get<SettingsSchema>('/api/v1/settings/agent-schema');
  }

  getConversationSettingsSchema(): Promise<SettingsSchema> {
    return this.get<SettingsSchema>('/api/v1/settings/conversation-schema');
  }

  listProfiles(): Promise<ProfileListResponse> {
    return this.get<ProfileListResponse>(this.profileBasePath());
  }

  async getProfile(name: string): Promise<ProfileDetailResponse> {
    const result = await this.get<{
      name: string;
      config?: Record<string, unknown>;
      llm?: Record<string, unknown>;
      api_key_set?: boolean;
    }>(`${this.profileBasePath()}/${encodeURIComponent(name)}`);
    return {
      name: result.name,
      config: result.config ?? result.llm ?? {},
      api_key_set: result.api_key_set ?? false,
    };
  }

  saveProfile(name: string, request: SaveProfileRequest): Promise<ProfileMutationResponse> {
    return this.post<ProfileMutationResponse>(
      `${this.profileBasePath()}/${encodeURIComponent(name)}`,
      request
    );
  }

  deleteProfile(name: string): Promise<ProfileMutationResponse> {
    return this.delete<ProfileMutationResponse>(
      `${this.profileBasePath()}/${encodeURIComponent(name)}`
    );
  }

  renameProfile(name: string, newName: string): Promise<ProfileMutationResponse> {
    return this.post<ProfileMutationResponse>(
      `${this.profileBasePath()}/${encodeURIComponent(name)}/rename`,
      { new_name: newName }
    );
  }

  async activateProfile(name: string): Promise<ActivateProfileResponse> {
    const result = await this.post<{
      name: string;
      message: string;
      model?: string | null;
      llm?: Record<string, unknown> | null;
    }>(`${this.profileBasePath()}/${encodeURIComponent(name)}/activate`, {});
    return {
      name: result.name,
      message: result.message,
      llm_applied: result.model != null || result.llm != null,
    };
  }

  async listSecrets(): Promise<CloudSecretWithoutValue[]> {
    const secrets: CloudSecretWithoutValue[] = [];
    let pageId: string | null = null;
    do {
      const query = new URLSearchParams({ limit: String(DEFAULT_PAGE_LIMIT) });
      if (pageId) query.set('page_id', pageId);
      const page = await this.get<CloudSecretsPage>(`/api/v1/secrets/search?${query.toString()}`);
      secrets.push(...(page.items ?? []));
      pageId = page.next_page_id;
    } while (pageId);
    return secrets;
  }

  async createSecret(name: string, value: string, description?: string): Promise<void> {
    await this.post('/api/v1/secrets', { name, value, description });
  }

  async updateSecret(secretToEdit: string, name: string, description?: string): Promise<void> {
    await this.put(`/api/v1/secrets/${encodeURIComponent(secretToEdit)}`, {
      name,
      description,
    });
  }

  async deleteSecret(name: string): Promise<void> {
    await this.delete(`/api/v1/secrets/${encodeURIComponent(name)}`);
  }

  async listSkills(): Promise<CloudSkillInfo[]> {
    const skills: CloudSkillInfo[] = [];
    let pageId: string | null = null;
    do {
      const query = new URLSearchParams({ limit: String(DEFAULT_PAGE_LIMIT) });
      if (pageId) query.set('page_id', pageId);
      const page = await this.get<CloudSkillsPage>(`/api/v1/skills/search?${query.toString()}`);
      skills.push(...(page.items ?? []));
      pageId = page.next_page_id;
    } while (pageId);
    return skills;
  }

  searchRepositories(args: {
    provider: string;
    query?: string;
    limit?: number;
    pageId?: string;
    installationId?: string;
  }): Promise<CloudRepositoryPage> {
    const params = new URLSearchParams();
    params.set('provider', args.provider);
    params.set('limit', String(args.limit ?? 100));
    if (args.query) params.set('query', args.query);
    if (args.pageId) params.set('page_id', args.pageId);
    if (args.installationId) params.set('installation_id', args.installationId);
    return this.get<CloudRepositoryPage>(`/api/v1/git/repositories/search?${params.toString()}`);
  }

  getInstallations(args: {
    provider: string;
    pageId?: string;
    limit?: number;
  }): Promise<CloudInstallationPage> {
    const params = new URLSearchParams();
    params.set('provider', args.provider);
    params.set('limit', String(args.limit ?? 100));
    if (args.pageId) params.set('page_id', args.pageId);
    return this.get<CloudInstallationPage>(`/api/v1/git/installations/search?${params.toString()}`);
  }

  getRepositoryBranches(args: {
    provider: string;
    repository: string;
    query?: string;
    pageId?: string;
    limit?: number;
  }): Promise<CloudBranchPage> {
    const params = new URLSearchParams();
    params.set('provider', args.provider);
    params.set('repository', args.repository);
    params.set('limit', String(args.limit ?? 30));
    params.set('query', args.query ?? '');
    if (args.pageId) params.set('page_id', args.pageId);
    return this.get<CloudBranchPage>(`/api/v1/git/branches/search?${params.toString()}`);
  }

  getSuggestedTasks(
    args: {
      pageId?: string;
      limit?: number;
    } = {}
  ): Promise<CloudPage<CloudSuggestedTask>> {
    const params = new URLSearchParams();
    params.set('limit', String(args.limit ?? 30));
    if (args.pageId) params.set('page_id', args.pageId);
    return this.get<CloudPage<CloudSuggestedTask>>(
      `/api/v1/git/suggested-tasks/search?${params.toString()}`
    );
  }

  searchModels<TPage = unknown>(params: Record<string, string | number | boolean | undefined>) {
    return this.get<TPage>(`/api/v1/config/models/search${buildQuerySuffix(params)}`);
  }

  searchProviders<TPage = unknown>(params: Record<string, string | number | boolean | undefined>) {
    return this.get<TPage>(`/api/v1/config/providers/search${buildQuerySuffix(params)}`);
  }

  searchConversations(limit = 20, pageId?: string): Promise<CloudConversationPage> {
    const params = new URLSearchParams();
    params.set('limit', String(limit));
    if (pageId) params.set('page_id', pageId);
    params.set('sort_order', 'UPDATED_AT_DESC');
    return this.get<CloudConversationPage>(`/api/v1/app-conversations/search?${params.toString()}`);
  }

  getConversations(ids: string[]): Promise<Array<CloudAppConversation | null>> {
    if (ids.length === 0) return Promise.resolve([]);
    const params = new URLSearchParams();
    for (const id of ids) params.append('ids', id);
    return this.get<Array<CloudAppConversation | null>>(`/api/v1/app-conversations?${params}`);
  }

  createConversation(request: CloudConversationStartRequest): Promise<CloudConversationStartTask> {
    return this.post<CloudConversationStartTask>('/api/v1/app-conversations', request);
  }

  downloadConversation(conversationId: string): Promise<Blob> {
    return this.get<Blob>(`/api/v1/app-conversations/${conversationId}/download`, {
      responseType: 'blob',
    });
  }

  async deleteConversation(conversationId: string): Promise<void> {
    await this.delete(`/api/v1/app-conversations/${conversationId}`);
  }

  updateConversationPublicFlag(
    conversationId: string,
    isPublic: boolean
  ): Promise<CloudAppConversation> {
    return this.patch<CloudAppConversation>(`/api/v1/app-conversations/${conversationId}`, {
      public: isPublic,
    });
  }

  async pauseSandbox(sandboxId: string): Promise<void> {
    await this.post(`/api/v1/sandboxes/${sandboxId}/pause`, {});
  }

  async resumeSandbox(sandboxId: string): Promise<void> {
    await this.post(`/api/v1/sandboxes/${sandboxId}/resume`, {});
  }

  getSandboxes(ids: string[]): Promise<Array<CloudSandboxInfo | null>> {
    if (ids.length === 0) return Promise.resolve([]);
    const params = new URLSearchParams();
    for (const id of ids) params.append('id', id);
    return this.get<Array<CloudSandboxInfo | null>>(`/api/v1/sandboxes?${params}`);
  }

  readConversationFile(conversationId: string, filePath: string): Promise<string> {
    const query = new URLSearchParams({ file_path: filePath });
    return this.get<string>(`/api/v1/app-conversations/${conversationId}/file?${query}`, {
      responseType: 'text',
    });
  }

  async getConversationStartTask(taskId: string): Promise<CloudConversationStartTask | null> {
    const params = new URLSearchParams();
    params.append('ids', taskId);
    const data = await this.get<Array<CloudConversationStartTask | null>>(
      `/api/v1/app-conversations/start-tasks?${params}`
    );
    return data?.[0] ?? null;
  }

  async switchConversationProfile(conversationId: string, profileName: string): Promise<void> {
    await this.post(`/api/v1/app-conversations/${conversationId}/switch_profile`, {
      profile_name: profileName,
    });
  }

  async switchConversationAcpModel(conversationId: string, model: string): Promise<void> {
    await this.post(`/api/v1/app-conversations/${conversationId}/switch_acp_model`, { model });
  }

  async listAutomations<TResponse = unknown>(limit = 50, offset = 0): Promise<TResponse> {
    return this.get<TResponse>(`/api/automation/v1?${paginationQuery(limit, offset)}`);
  }

  getAutomation<TResponse = unknown>(id: string): Promise<TResponse> {
    return this.get<TResponse>(`/api/automation/v1/${encodeURIComponent(id)}`);
  }

  updateAutomation<TResponse = unknown>(id: string, body: unknown): Promise<TResponse> {
    return this.patch<TResponse>(`/api/automation/v1/${encodeURIComponent(id)}`, body);
  }

  async deleteAutomation(id: string): Promise<void> {
    await this.delete(`/api/automation/v1/${encodeURIComponent(id)}`);
  }

  dispatchAutomation<TResponse = unknown>(id: string): Promise<TResponse> {
    return this.post<TResponse>(`/api/automation/v1/${encodeURIComponent(id)}/dispatch`);
  }

  listAutomationRuns<TResponse = unknown>(id: string, limit = 50, offset = 0): Promise<TResponse> {
    return this.get<TResponse>(
      `/api/automation/v1/${encodeURIComponent(id)}/runs?${paginationQuery(limit, offset)}`
    );
  }

  downloadAutomationTarball(id: string): Promise<Blob> {
    return this.get<Blob>(`/api/automation/v1/${encodeURIComponent(id)}/tarball`, {
      responseType: 'blob',
    });
  }

  getAutomationHealth<TResponse = unknown>(): Promise<TResponse> {
    return this.get<TResponse>('/api/automation/health', { timeoutSeconds: 5 });
  }

  private profileBasePath(): string {
    return this.orgId
      ? `/api/organizations/${encodeURIComponent(this.orgId)}/profiles`
      : SETTINGS_PROFILES_PATH;
  }

  private buildUpstreamAuthHeaders(options: CloudRequestOptions): Record<string, string> {
    const mode = options.authMode ?? 'bearer';
    if (mode === 'none') return {};
    if (mode === 'session-api-key') {
      return options.sessionApiKey ? { 'X-Session-API-Key': options.sessionApiKey } : {};
    }
    if (!this.apiKey) return {};
    return { Authorization: `Bearer ${this.apiKey}` };
  }

  private async requestDirect<TResponse>(options: CloudRequestOptions): Promise<TResponse> {
    const headers = {
      ...this.buildUpstreamAuthHeaders(options),
      ...(this.orgId ? { 'X-Org-Id': this.orgId } : {}),
      ...(options.headers ?? {}),
    };
    return fetchAndParse<TResponse>({
      host: this.host,
      method: options.method,
      path: options.path,
      params: options.params,
      body: options.body,
      headers,
      timeoutMs: options.timeoutSeconds ? options.timeoutSeconds * 1000 : this.timeout,
      acceptableStatusCodes: options.acceptableStatusCodes,
      responseType: options.responseType,
    });
  }

  private async requestThroughProxy<TResponse>(options: CloudRequestOptions): Promise<TResponse> {
    if (!this.proxy) {
      throw new Error('CloudClient proxy options are required for hostOverride requests');
    }

    const upstreamHeaders = {
      ...this.buildUpstreamAuthHeaders(options),
      ...(this.orgId ? { 'X-Org-Id': this.orgId } : {}),
      ...(options.headers ?? {}),
    };
    const proxyHeaders = {
      'Content-Type': 'application/json',
      ...(this.proxy.apiKey ? { 'X-Session-API-Key': this.proxy.apiKey } : {}),
      ...(this.proxy.headers ?? {}),
    };
    const proxyPath = this.proxy.path ?? '/api/cloud-proxy';
    return fetchAndParse<TResponse>({
      host: this.proxy.host,
      method: 'POST',
      path: proxyPath,
      body: {
        host: options.hostOverride,
        method: options.method,
        path: appendParams(options.path, options.params),
        headers: upstreamHeaders,
        body: options.body ?? null,
        ...(options.timeoutSeconds ? { timeout_seconds: options.timeoutSeconds } : {}),
      },
      headers: proxyHeaders,
      timeoutMs: this.timeout,
      acceptableStatusCodes: options.acceptableStatusCodes,
      responseType: options.responseType,
    });
  }
}

interface FetchAndParseOptions {
  host: string;
  method: string;
  path: string;
  params?: Record<string, unknown>;
  body?: unknown;
  headers?: Record<string, string>;
  timeoutMs: number;
  acceptableStatusCodes?: Set<number>;
  responseType?: ResponseType;
}

async function fetchAndParse<TResponse>(options: FetchAndParseOptions): Promise<TResponse> {
  const url = `${options.host.replace(/\/+$/, '')}${appendParams(options.path, options.params)}`;
  const headers: Record<string, string> = {
    ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
    ...(options.headers ?? {}),
  };
  const init: RequestInit = {
    method: options.method,
    headers,
    signal: AbortSignal.timeout(options.timeoutMs),
  };
  if (options.body !== undefined && options.method !== 'GET') {
    init.body = options.body instanceof FormData ? options.body : JSON.stringify(options.body);
  }

  let response: Response;
  try {
    response = await fetch(url, init);
  } catch (error) {
    // AbortSignal.timeout() aborts with a TimeoutError; some runtimes
    // surface plain AbortError instead.
    if (error instanceof Error && (error.name === 'TimeoutError' || error.name === 'AbortError')) {
      throw new Error(`Request timeout after ${options.timeoutMs}ms`, { cause: error });
    }
    throw error;
  }

  const isAcceptable =
    options.acceptableStatusCodes?.has(response.status) ||
    (!options.acceptableStatusCodes && response.ok);
  if (!isAcceptable) {
    throw await buildHttpError(response);
  }

  return parseResponse<TResponse>(response, options.responseType ?? 'auto');
}

async function buildHttpError(response: Response): Promise<HttpError> {
  let errorContent: unknown;
  try {
    const contentType = response.headers.get('content-type');
    errorContent = contentType?.includes('application/json')
      ? await response.json()
      : await response.text();
  } catch {
    errorContent = null;
  }
  return new HttpError(
    response.status,
    response.statusText,
    errorContent,
    `HTTP request failed (${response.status} ${response.statusText}): ${JSON.stringify(
      errorContent
    )}`
  );
}

async function parseResponse<TResponse>(
  response: Response,
  responseType: ResponseType
): Promise<TResponse> {
  if (response.status === 204) {
    return undefined as TResponse;
  }
  if (responseType === 'blob') return (await response.blob()) as TResponse;
  if (responseType === 'arrayBuffer') return (await response.arrayBuffer()) as TResponse;
  if (responseType === 'text') return (await response.text()) as TResponse;

  const contentType = response.headers.get('content-type');
  if (responseType === 'json' || contentType?.includes('application/json')) {
    return (await response.json()) as TResponse;
  }
  return (await response.text()) as TResponse;
}

function appendParams(path: string, params?: Record<string, unknown>): string {
  if (!params) return path;
  const [base, existingQuery = ''] = path.split('?');
  const search = new URLSearchParams(existingQuery);
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) {
      value.forEach((item) => search.append(key, String(item)));
    } else {
      search.append(key, String(value));
    }
  }
  const query = search.toString();
  return query ? `${base}?${query}` : base;
}

function buildQuerySuffix(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value));
  }
  const query = search.toString();
  return query ? `?${query}` : '';
}

function paginationQuery(limit: number, offset: number): string {
  const params = new URLSearchParams();
  params.set('limit', String(limit));
  params.set('offset', String(offset));
  return params.toString();
}

function deriveAgentSettings(flat: CloudSettingsResponse): Record<string, CloudSettingsValue> {
  if (flat.agent_settings && Object.keys(flat.agent_settings).length > 0) {
    return flat.agent_settings;
  }
  const agent: Record<string, CloudSettingsValue> = {};
  const llm: Record<string, CloudSettingsValue> = {};
  if (typeof flat.llm_model === 'string') llm.model = flat.llm_model;
  if (typeof flat.llm_base_url === 'string') llm.base_url = flat.llm_base_url;
  if (typeof flat.llm_api_key === 'string') llm.api_key = flat.llm_api_key;
  if (Object.keys(llm).length > 0) agent.llm = llm;

  const condenser: Record<string, CloudSettingsValue> = {};
  if (typeof flat.enable_default_condenser === 'boolean') {
    condenser.enabled = flat.enable_default_condenser;
  }
  if (typeof flat.condenser_max_size === 'number') {
    condenser.max_size = flat.condenser_max_size;
  }
  if (Object.keys(condenser).length > 0) agent.condenser = condenser;

  if (typeof flat.agent === 'string') agent.agent = flat.agent;
  if (flat.mcp_config && Object.keys(flat.mcp_config).length > 0) {
    agent.mcp_config = flat.mcp_config;
  }
  return agent;
}

function deriveConversationSettings(
  flat: CloudSettingsResponse
): Record<string, CloudSettingsValue> {
  if (flat.conversation_settings && Object.keys(flat.conversation_settings).length > 0) {
    return flat.conversation_settings;
  }
  const out: Record<string, CloudSettingsValue> = {};
  if (typeof flat.confirmation_mode === 'boolean') {
    out.confirmation_mode = flat.confirmation_mode;
  }
  if (typeof flat.security_analyzer === 'string' || flat.security_analyzer === null) {
    out.security_analyzer = flat.security_analyzer;
  }
  if (typeof flat.max_iterations === 'number') {
    out.max_iterations = flat.max_iterations;
  }
  return out;
}
