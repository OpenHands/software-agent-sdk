import { HttpClient } from './http-client';
import {
  ActivateProfileResponse,
  ExposeSecretsMode,
  ProfileDetailResponse,
  ProfileListResponse,
  ProfileMutationResponse,
  SaveProfileRequest,
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

  close(): void {
    this.client.close();
  }
}
