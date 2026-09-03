import {
  AgentServerFeatureRequirements,
  assertAgentServerSupports,
} from './agent-server-compatibility';
import { HttpClient } from './http-client';
import type { HooksRequest, HooksResponse } from '../models/api';

export interface HooksClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export class HooksClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: HooksClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  async loadHooks(request: HooksRequest = {}): Promise<HooksResponse> {
    await assertAgentServerSupports(this.client, AgentServerFeatureRequirements.hooks);
    const response = await this.client.post<HooksResponse>('/api/hooks', request);
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
