import {
  AgentServerFeatureRequirements,
  assertAgentServerSupports,
} from './agent-server-compatibility';
import { HttpClient } from './http-client';
import type { MCPTestRequest, MCPTestResponse } from '../models/api';

export interface MCPClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export class MCPClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: MCPClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  async testServer(request: MCPTestRequest): Promise<MCPTestResponse> {
    await assertAgentServerSupports(this.client, AgentServerFeatureRequirements.mcpTest);
    const response = await this.client.post<MCPTestResponse>('/api/mcp/test', request, {
      timeout: (request.timeout ?? 15) * 1000 + 5000,
    });
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
