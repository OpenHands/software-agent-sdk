import { HttpClient } from './http-client';
import type { ToolCatalogResponse } from '../models/tool-catalog';

export interface ToolClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export class ToolClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: ToolClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  async listTools(): Promise<string[]> {
    const response = await this.client.get<string[]>('/api/tools/');
    return response.data;
  }

  /** Tools a client may offer when configuring an agent. */
  async getToolCatalog(): Promise<ToolCatalogResponse> {
    const response = await this.client.get<ToolCatalogResponse>('/api/tools/catalog');
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
