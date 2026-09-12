import { runtimeServiceConnections } from './runtime-transport';
import type { RuntimeServiceOptions } from './runtime-transport';
import type { HttpClient } from './http-client';
import { DesktopUrlResponse } from '../models/api';

export type DesktopClientOptions = RuntimeServiceOptions;

export class DesktopClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: DesktopClientOptions) {
    const { server, runtime } = runtimeServiceConnections(options);
    this.host = server.host;
    this.apiKey = server.sessionApiKey;
    this.client = runtime;
  }

  async getUrl(baseUrl?: string): Promise<string | null> {
    const response = await this.client.get<DesktopUrlResponse>('/api/desktop/url', {
      params: baseUrl ? { base_url: baseUrl } : undefined,
    });
    return response.data.url;
  }

  close(): void {
    this.client.close();
  }
}
