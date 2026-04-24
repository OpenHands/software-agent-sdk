import { HttpClient } from './http-client';
import { SkillsRequest, SkillsResponse, SyncResponse } from '../models/api';

export interface SkillsClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export class SkillsClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: SkillsClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  async getSkills(request: SkillsRequest = {}): Promise<SkillsResponse> {
    const response = await this.client.post<SkillsResponse>('/api/skills', request);
    return response.data;
  }

  async syncSkills(): Promise<SyncResponse> {
    const response = await this.client.post<SyncResponse>('/api/skills/sync', {});
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
