import { HttpClient } from './http-client';
import type { ApiKey, CreateApiKeyResponse } from '../models/api';

export interface ApiKeysClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export class ApiKeysClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: ApiKeysClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  async listApiKeys(): Promise<ApiKey[]> {
    const response = await this.client.get<unknown>('/api/keys');
    return Array.isArray(response.data) ? (response.data as ApiKey[]) : [];
  }

  async createApiKey(name: string): Promise<CreateApiKeyResponse> {
    const response = await this.client.post<CreateApiKeyResponse>('/api/keys', { name });
    return response.data;
  }

  async deleteApiKey(id: string): Promise<void> {
    await this.client.delete(`/api/keys/${encodeURIComponent(id)}`);
  }

  close(): void {
    this.client.close();
  }
}
