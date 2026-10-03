import { HttpClient } from './http-client';
import {
  ActivateProfileResponse,
  ExposeSecretsMode,
  ProfileDetailResponse,
  ProfileListResponse,
  ProfileMutationResponse,
  SaveProfileRequest,
  ValidateProfileResponse,
} from '../models/api';

export interface ProfilesClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export interface GetProfileOptions {
  /**
   * Controls server-side secret exposure via the ``X-Expose-Secrets`` header:
   * - ``encrypted``: cipher-encrypted values (safe for frontend round-trip)
   * - ``plaintext``: raw secret values (backend clients only)
   * - omitted: ``api_key`` is nulled, with ``api_key_set`` indicator
   */
  exposeSecrets?: ExposeSecretsMode;
}

export class ProfilesClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: ProfilesClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  async listProfiles(): Promise<ProfileListResponse> {
    const response = await this.client.get<ProfileListResponse>('/api/profiles');
    return response.data;
  }

  async getProfile(name: string, options: GetProfileOptions = {}): Promise<ProfileDetailResponse> {
    const headers: Record<string, string> = {};
    if (options.exposeSecrets) {
      headers['X-Expose-Secrets'] = options.exposeSecrets;
    }
    const response = await this.client.get<ProfileDetailResponse>(
      `/api/profiles/${encodeURIComponent(name)}`,
      { headers }
    );
    return response.data;
  }

  async saveProfile(name: string, request: SaveProfileRequest): Promise<ProfileMutationResponse> {
    const response = await this.client.post<ProfileMutationResponse>(
      `/api/profiles/${encodeURIComponent(name)}`,
      request
    );
    return response.data;
  }

  async deleteProfile(name: string): Promise<ProfileMutationResponse> {
    const response = await this.client.delete<ProfileMutationResponse>(
      `/api/profiles/${encodeURIComponent(name)}`
    );
    return response.data;
  }

  async renameProfile(name: string, newName: string): Promise<ProfileMutationResponse> {
    const response = await this.client.post<ProfileMutationResponse>(
      `/api/profiles/${encodeURIComponent(name)}/rename`,
      { new_name: newName }
    );
    return response.data;
  }

  async activateProfile(name: string): Promise<ActivateProfileResponse> {
    const response = await this.client.post<ActivateProfileResponse>(
      `/api/profiles/${encodeURIComponent(name)}/activate`,
      {}
    );
    return response.data;
  }

  /**
   * Pre-flight check: fire a minimal LLM completion (``ping``,
   * ``max_tokens=1``) with the submitted config to catch misconfigurations
   * (invalid model names, missing provider prefixes, bad base URLs, invalid
   * API keys) before a profile is saved.
   *
   * A structured ``{ valid: false, error: { type, message } }`` response is
   * returned on blocking errors. Transient errors (rate limits, timeouts) are
   * non-blocking (``valid: true``). Older agent-server versions that do not
   * implement the endpoint respond 404, which surfaces as an ``HttpError`` so
   * callers can treat it as "no verdict" and proceed.
   */
  async validateProfile(
    name: string,
    request: SaveProfileRequest
  ): Promise<ValidateProfileResponse> {
    const response = await this.client.post<ValidateProfileResponse>(
      `/api/profiles/${encodeURIComponent(name)}/validate`,
      request
    );
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
