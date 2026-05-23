import {
  AgentServerFeatureRequirements,
  assertAgentServerSupports,
} from './agent-server-compatibility';
import { HttpClient } from './http-client';
import type { CloudProxyRequest } from '../models/api';

export interface CloudProxyClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export class CloudProxyClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: CloudProxyClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  async forward<TResponse = unknown>(request: CloudProxyRequest): Promise<TResponse> {
    await assertAgentServerSupports(this.client, AgentServerFeatureRequirements.cloudProxy);
    const response = await this.client.post<TResponse>('/api/cloud-proxy', request, {
      timeout: (request.timeout_seconds ?? 15) * 1000 + 5000,
    });
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
