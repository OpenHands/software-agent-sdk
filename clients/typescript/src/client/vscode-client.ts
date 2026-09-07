import { HttpClient } from './http-client';
import { VSCodeStatusResponse, VSCodeUrlResponse } from '../models/api';

export interface VSCodeClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export interface GetVSCodeUrlOptions {
  baseUrl?: string;
  workspaceDir?: string;
}

export class VSCodeClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: VSCodeClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
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
