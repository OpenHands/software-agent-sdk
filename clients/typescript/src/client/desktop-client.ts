import { createRuntimeHttpClients } from './runtime-transport';
import type { RuntimeServiceClientOptions } from './runtime-transport';
import type { HttpClient } from './http-client';
import { DesktopUrlResponse } from '../models/api';

export type DesktopClientOptions = RuntimeServiceClientOptions;

export class DesktopClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: DesktopClientOptions) {
    const { runtimeClient } = createRuntimeHttpClients(options);
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = runtimeClient;
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
