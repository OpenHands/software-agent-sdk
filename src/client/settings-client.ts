import { HttpClient } from './http-client';
import { SettingsSchema } from '../models/api';

export interface SettingsClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export class SettingsClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: SettingsClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  async getAgentSchema(): Promise<SettingsSchema> {
    const response = await this.client.get<SettingsSchema>('/api/settings/agent-schema');
    return response.data;
  }

  async getConversationSchema(): Promise<SettingsSchema> {
    const response = await this.client.get<SettingsSchema>('/api/settings/conversation-schema');
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
