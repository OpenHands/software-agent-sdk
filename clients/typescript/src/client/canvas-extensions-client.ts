import { HttpClient } from './http-client';
import type {
  AgentServerAppBackendSessionResponse,
  AgentServerCanvasBackendDataDeleteResponse,
  AgentServerCanvasBackendLogsResponse,
  AgentServerCanvasBackendPrepareResponse,
  AgentServerCanvasBackendStartResponse,
  AgentServerCanvasBackendStatusResponse,
  AgentServerCanvasBackendStopResponse,
} from '../models/agent-server-api';

export interface CanvasExtensionsClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
  appBackendIngressUrl?: string;
}

export class CanvasExtensionsClient {
  public readonly host: string;
  public readonly apiKey?: string;
  public readonly appBackendIngressUrl?: string;
  private readonly client: HttpClient;
  private readonly appBackendClient?: HttpClient;

  constructor(options: CanvasExtensionsClientOptions) {
    this.host = options.host.replace(/\/+$/, '');
    this.apiKey = options.apiKey;
    this.appBackendIngressUrl = options.appBackendIngressUrl?.replace(/\/+$/, '');
    const timeout = options.timeout || 60000;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout,
    });
    if (this.appBackendIngressUrl) {
      this.appBackendClient = new HttpClient({
        baseUrl: this.appBackendIngressUrl,
        apiKey: this.apiKey,
        timeout,
      });
    }
  }

  private backendPath(name: string, suffix = ''): string {
    return `/api/canvas-extensions/installed/${encodeURIComponent(name)}/backend${suffix}`;
  }

  async getBackendStatus(name: string): Promise<AgentServerCanvasBackendStatusResponse> {
    const response = await this.client.get<AgentServerCanvasBackendStatusResponse>(
      this.backendPath(name)
    );
    return response.data;
  }

  async prepareBackend(
    name: string,
    revision: string
  ): Promise<AgentServerCanvasBackendPrepareResponse> {
    const response = await this.client.post<AgentServerCanvasBackendPrepareResponse>(
      this.backendPath(name, '/prepare'),
      { revision }
    );
    return response.data;
  }

  async startBackend(
    name: string,
    revision: string
  ): Promise<AgentServerCanvasBackendStartResponse> {
    const response = await this.client.post<AgentServerCanvasBackendStartResponse>(
      this.backendPath(name, '/start'),
      { revision }
    );
    return response.data;
  }

  async stopBackend(name: string): Promise<AgentServerCanvasBackendStopResponse> {
    const response = await this.client.post<AgentServerCanvasBackendStopResponse>(
      this.backendPath(name, '/stop')
    );
    return response.data;
  }

  async getBackendLogs(
    name: string,
    limitBytes = 64 * 1024
  ): Promise<AgentServerCanvasBackendLogsResponse> {
    const response = await this.client.get<AgentServerCanvasBackendLogsResponse>(
      this.backendPath(name, '/logs'),
      { params: { limit_bytes: limitBytes } }
    );
    return response.data;
  }

  async deleteBackendData(name: string): Promise<AgentServerCanvasBackendDataDeleteResponse> {
    const response = await this.client.delete<AgentServerCanvasBackendDataDeleteResponse>(
      this.backendPath(name, '/data')
    );
    return response.data;
  }

  async createAppBackendSession(name: string): Promise<AgentServerAppBackendSessionResponse> {
    const client = this.requireAppBackendClient();
    const response = await client.post<AgentServerAppBackendSessionResponse>(
      `/app-backends/${encodeURIComponent(name)}/session`,
      undefined,
      { credentials: 'include' }
    );
    return response.data;
  }

  async revokeAppBackendSession(name: string): Promise<void> {
    const client = this.requireAppBackendClient();
    await client.delete(`/app-backends/${encodeURIComponent(name)}/session`, {
      credentials: 'include',
      acceptableStatusCodes: new Set([204]),
    });
  }

  private requireAppBackendClient(): HttpClient {
    if (!this.appBackendClient) {
      throw new Error(
        'Canvas App backend ingress is unavailable; use app_backend_ingress_url from /server_info'
      );
    }
    return this.appBackendClient;
  }

  close(): void {
    this.client.close();
    this.appBackendClient?.close();
  }
}
