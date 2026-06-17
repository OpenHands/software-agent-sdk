import { HttpClient } from './http-client';
import {
  ActivateMetaProfileResponse,
  MetaProfile,
  MetaProfileDetailResponse,
  MetaProfileListResponse,
  MetaProfileMutationResponse,
} from '../models/api';

export interface MetaProfilesClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

/**
 * Client for the agent-server ``/api/meta-profiles`` endpoints.
 *
 * A meta-profile is a model-routing configuration consumed by the
 * ``classify_and_switch_llm`` tool. Unlike LLM profiles, meta-profiles hold no
 * secrets — they are plain JSON documents — so there is no secret-exposure
 * handling here.
 */
export class MetaProfilesClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: MetaProfilesClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  async listMetaProfiles(): Promise<MetaProfileListResponse> {
    const response = await this.client.get<MetaProfileListResponse>('/api/meta-profiles');
    return response.data;
  }

  async getMetaProfile(name: string): Promise<MetaProfileDetailResponse> {
    const response = await this.client.get<MetaProfileDetailResponse>(
      `/api/meta-profiles/${encodeURIComponent(name)}`
    );
    return response.data;
  }

  async saveMetaProfile(name: string, config: MetaProfile): Promise<MetaProfileMutationResponse> {
    const response = await this.client.post<MetaProfileMutationResponse>(
      `/api/meta-profiles/${encodeURIComponent(name)}`,
      config
    );
    return response.data;
  }

  async deleteMetaProfile(name: string): Promise<MetaProfileMutationResponse> {
    const response = await this.client.delete<MetaProfileMutationResponse>(
      `/api/meta-profiles/${encodeURIComponent(name)}`
    );
    return response.data;
  }

  async activateMetaProfile(name: string): Promise<ActivateMetaProfileResponse> {
    const response = await this.client.post<ActivateMetaProfileResponse>(
      `/api/meta-profiles/${encodeURIComponent(name)}/activate`,
      {}
    );
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
