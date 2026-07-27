import { HttpClient } from './http-client';
import type {
  ExposeSecretsMode as ApiExposeSecretsMode,
  ProfileDetailResponse,
  ProfileInfo,
  ProfileListResponse,
  ProfileMutationResponse,
  SaveProfileRequest,
  SettingsUpdateRequest as LegacySettingsUpdateRequest,
  SecretValueResponse,
  SecretsListResponse,
  UpsertSecretRequest,
  UpsertSecretResponse,
  DeleteSecretResponse,
} from '../models/api';
import type {
  AgentServerConversationSettingsSchema,
  AgentServerSettingsPatchRequest,
  AgentServerSettingsPatchResponse,
  AgentServerSettingsResponse,
  AgentServerSettingsSchema,
} from '../models/agent-server-api';

export interface SettingsClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export type ExposeSecretsMode = ApiExposeSecretsMode;
export type LLMProfileSummary = ProfileInfo;
export type LLMProfileListResponse = ProfileListResponse;
export type LLMProfileDetailResponse = ProfileDetailResponse;
export type SaveLLMProfileRequest = SaveProfileRequest;
export type LLMProfileMutationResponse = ProfileMutationResponse;

export class SettingsClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: SettingsClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  async getAgentSchema(): Promise<AgentServerSettingsSchema> {
    const response = await this.client.get<AgentServerSettingsSchema>('/api/settings/agent-schema');
    return response.data;
  }

  async getSettings(
    options: { exposeSecrets?: ExposeSecretsMode } = {}
  ): Promise<AgentServerSettingsResponse> {
    const response = await this.client.get<AgentServerSettingsResponse>('/api/settings', {
      headers: options.exposeSecrets ? { 'X-Expose-Secrets': options.exposeSecrets } : undefined,
    });
    return response.data;
  }

  /**
   * @deprecated Pass `AgentServerSettingsPatchRequest` for generated
   * contract checking. This overload keeps existing extension-shaped callers
   * source compatible while they migrate.
   */
  async updateSettings(
    request: LegacySettingsUpdateRequest
  ): Promise<AgentServerSettingsPatchResponse>;
  async updateSettings(
    request: AgentServerSettingsPatchRequest
  ): Promise<AgentServerSettingsPatchResponse>;
  async updateSettings(
    request: LegacySettingsUpdateRequest | AgentServerSettingsPatchRequest
  ): Promise<AgentServerSettingsPatchResponse> {
    const response = await this.client.patch<AgentServerSettingsPatchResponse>(
      '/api/settings',
      request
    );
    return response.data;
  }

  async listSecrets(): Promise<SecretsListResponse> {
    const response = await this.client.get<SecretsListResponse>('/api/settings/secrets');
    return response.data;
  }

  async upsertSecret(request: UpsertSecretRequest): Promise<UpsertSecretResponse> {
    const response = await this.client.put<UpsertSecretResponse>('/api/settings/secrets', request);
    return response.data;
  }

  async getSecret(name: string): Promise<SecretValueResponse> {
    const response = await this.client.get<SecretValueResponse>(
      `/api/settings/secrets/${encodeURIComponent(name)}`,
      { responseType: 'text' }
    );
    return response.data;
  }

  async deleteSecret(name: string): Promise<DeleteSecretResponse> {
    const response = await this.client.delete<DeleteSecretResponse>(
      `/api/settings/secrets/${encodeURIComponent(name)}`
    );
    return response.data;
  }

  async getConversationSchema(): Promise<AgentServerConversationSettingsSchema> {
    const response = await this.client.get<AgentServerConversationSettingsSchema>(
      '/api/settings/conversation-schema'
    );
    return response.data;
  }

  async listProfiles(): Promise<LLMProfileListResponse> {
    const response = await this.client.get<LLMProfileListResponse>('/api/profiles');
    return response.data;
  }

  async getProfile(
    name: string,
    options: { exposeSecrets?: ExposeSecretsMode } = {}
  ): Promise<LLMProfileDetailResponse> {
    const response = await this.client.get<LLMProfileDetailResponse>(
      `/api/profiles/${encodeURIComponent(name)}`,
      {
        headers: options.exposeSecrets ? { 'X-Expose-Secrets': options.exposeSecrets } : undefined,
      }
    );
    return response.data;
  }

  async saveProfile(
    name: string,
    request: SaveLLMProfileRequest
  ): Promise<LLMProfileMutationResponse> {
    const response = await this.client.post<LLMProfileMutationResponse>(
      `/api/profiles/${encodeURIComponent(name)}`,
      request
    );
    return response.data;
  }

  async deleteProfile(name: string): Promise<LLMProfileMutationResponse> {
    const response = await this.client.delete<LLMProfileMutationResponse>(
      `/api/profiles/${encodeURIComponent(name)}`
    );
    return response.data;
  }

  async renameProfile(name: string, newName: string): Promise<LLMProfileMutationResponse> {
    const response = await this.client.post<LLMProfileMutationResponse>(
      `/api/profiles/${encodeURIComponent(name)}/rename`,
      { new_name: newName }
    );
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
