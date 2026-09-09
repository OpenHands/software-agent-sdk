import { HttpClient } from './http-client';
import { DesktopUrlResponse } from '../models/api';

export interface DesktopClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export class DesktopClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: DesktopClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
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
