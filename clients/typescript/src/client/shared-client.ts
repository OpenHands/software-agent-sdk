import { HttpClient } from './http-client';
import type { EventPage, SharedConversation } from '../models/api';

export interface SharedClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export interface SharedEventSearchOptions {
  conversationId: string;
  limit?: number;
  pageId?: string;
}

export class SharedClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: SharedClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  async getSharedConversations(ids: string[]): Promise<Array<SharedConversation | null>> {
    const response = await this.client.get<Array<SharedConversation | null>>(
      '/api/shared-conversations',
      { params: { ids } }
    );
    return response.data;
  }

  async getSharedConversation(id: string): Promise<SharedConversation | null> {
    const conversations = await this.getSharedConversations([id]);
    return conversations[0] ?? null;
  }

  async searchSharedEvents(options: SharedEventSearchOptions): Promise<EventPage> {
    const response = await this.client.get<EventPage>('/api/shared-events/search', {
      params: {
        conversation_id: options.conversationId,
        limit: options.limit,
        page_id: options.pageId,
      },
    });
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
