import { HttpClient } from './http-client';
import type { ACPModelDiscovery, ACPModelDiscoveryRequest } from '../models/acp-model-discovery';

export interface ACPClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export class ACPClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: ACPClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 180000,
    });
  }

  /** Start the ACP server in a throwaway session and report its default and models. */
  async discoverModels(request: ACPModelDiscoveryRequest): Promise<ACPModelDiscovery> {
    const response = await this.client.post<ACPModelDiscovery>('/api/acp/models', request);
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
