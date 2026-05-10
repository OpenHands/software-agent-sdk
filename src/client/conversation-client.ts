import { HttpClient } from './http-client';
import { Success } from '../types/base';

export interface ConversationClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export class ConversationClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: ConversationClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  async switchProfile(conversationId: string, profileName: string): Promise<void> {
    await this.client.post<Success>(`/api/conversations/${conversationId}/switch_profile`, {
      profile_name: profileName,
    });
  }

  close(): void {
    this.client.close();
  }
}
