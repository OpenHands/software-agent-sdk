import { runtimeServiceConnections } from './runtime-transport';
import type { RuntimeServiceOptions } from './runtime-transport';
import type { HttpClient } from './http-client';
import { VSCodeStatusResponse, VSCodeUrlResponse } from '../models/api';

export type VSCodeClientOptions = RuntimeServiceOptions;

export interface GetVSCodeUrlOptions {
  baseUrl?: string;
  workspaceDir?: string;
}

export class VSCodeClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: VSCodeClientOptions) {
    const { server, runtime } = runtimeServiceConnections(options);
    this.host = server.host;
    this.apiKey = server.sessionApiKey;
    this.client = runtime;
  }

  async getUrl(options: GetVSCodeUrlOptions = {}): Promise<string | null> {
    const response = await this.client.get<VSCodeUrlResponse>('/api/vscode/url', {
      params: {
        ...(options.baseUrl ? { base_url: options.baseUrl } : {}),
        ...(options.workspaceDir ? { workspace_dir: options.workspaceDir } : {}),
      },
    });
    return response.data.url;
  }

  async getStatus(): Promise<VSCodeStatusResponse> {
    const response = await this.client.get<VSCodeStatusResponse>('/api/vscode/status');
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
