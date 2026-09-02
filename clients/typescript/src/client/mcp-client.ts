import {
  AgentServerFeatureRequirements,
  assertAgentServerSupports,
} from './agent-server-compatibility';
import { HttpClient } from './http-client';
import type {
  AgentServerMCPOAuthCallbackRequest,
  AgentServerMCPOAuthCallbackResponse,
  AgentServerMCPOAuthStatusResponse,
  AgentServerMCPStartOAuthRequest,
  AgentServerMCPStartOAuthResponse,
  AgentServerMCPTestRequest,
  AgentServerMCPTestResponse,
} from '../models/agent-server-api';
import type {
  MCPOAuthStartResponse as LegacyMCPOAuthStartResponse,
  MCPTestRequest as LegacyMCPTestRequest,
  MCPTestResponse as LegacyMCPTestResponse,
} from '../models/api';

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

  async testServer(request: AgentServerMCPTestRequest): Promise<AgentServerMCPTestResponse>;
  /**
   * @deprecated Pass `AgentServerMCPTestRequest`. This overload only keeps
   * older request payloads source compatible while callers migrate.
   */
  async testServer(request: LegacyMCPTestRequest): Promise<LegacyMCPTestResponse>;
  async testServer(
    request: LegacyMCPTestRequest | AgentServerMCPTestRequest
  ): Promise<LegacyMCPTestResponse | AgentServerMCPTestResponse> {
    await assertAgentServerSupports(this.client, AgentServerFeatureRequirements.mcpTest);
    const response = await this.client.post<AgentServerMCPTestResponse>('/api/mcp/test', request, {
      timeout: (request.timeout ?? 15) * 1000 + 5000,
    });
    return response.data;
  }

  async startOAuth(
    request: AgentServerMCPStartOAuthRequest
  ): Promise<AgentServerMCPStartOAuthResponse>;
  /**
   * @deprecated Pass `AgentServerMCPStartOAuthRequest`. This overload only
   * keeps older request payloads source compatible while callers migrate.
   */
  async startOAuth(request: LegacyMCPTestRequest): Promise<LegacyMCPOAuthStartResponse>;
  async startOAuth(
    request: LegacyMCPTestRequest | AgentServerMCPStartOAuthRequest
  ): Promise<LegacyMCPOAuthStartResponse | AgentServerMCPStartOAuthResponse> {
    await assertAgentServerSupports(this.client, AgentServerFeatureRequirements.mcpOAuth);
    const response = await this.client.post<AgentServerMCPStartOAuthResponse>(
      '/api/mcp/oauth/start',
      request,
      {
        timeout: (request.timeout ?? 15) * 1000 + 5000,
      }
    );
    return response.data;
  }

  async getOAuthStatus(jobId: string): Promise<AgentServerMCPOAuthStatusResponse> {
    await assertAgentServerSupports(this.client, AgentServerFeatureRequirements.mcpOAuth);
    const response = await this.client.get<AgentServerMCPOAuthStatusResponse>(
      `/api/mcp/oauth/status/${encodeURIComponent(jobId)}`
    );
    return response.data;
  }

  async submitOAuthCallback(
    jobId: string,
    request: AgentServerMCPOAuthCallbackRequest
  ): Promise<AgentServerMCPOAuthCallbackResponse> {
    await assertAgentServerSupports(this.client, AgentServerFeatureRequirements.mcpOAuth);
    const response = await this.client.post<AgentServerMCPOAuthCallbackResponse>(
      `/api/mcp/oauth/callback/${encodeURIComponent(jobId)}`,
      request
    );
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
