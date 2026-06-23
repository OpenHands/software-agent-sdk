import { HttpClient } from './http-client';
import type { AcceptTosResponse } from '../models/api';

export interface SessionClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export class SessionClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: SessionClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  async acceptTos(redirectUrl: string): Promise<AcceptTosResponse> {
    const response = await this.client.post<AcceptTosResponse>('/api/accept_tos', {
      redirect_url: redirectUrl,
    });
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
