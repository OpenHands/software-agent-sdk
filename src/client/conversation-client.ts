import { HttpClient } from './http-client';
import { Success } from '../types/base';
import type {
  AskAgentResponse,
  ConfirmationResponseRequest,
  ConversationInfo,
  ConversationSearchRequest,
  ConversationSearchResponse,
  UpdateConversationRequest,
} from '../models/conversation';

export interface ConversationClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export type CreateConversationPayload = Record<string, unknown>;

export interface SendConversationEventOptions {
  run?: boolean;
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

  async createConversation<TConversation = ConversationInfo>(
    payload: CreateConversationPayload
  ): Promise<TConversation> {
    const response = await this.client.post<TConversation>('/api/conversations', payload);
    return response.data;
  }

  async searchConversations(
    options: ConversationSearchRequest = {}
  ): Promise<ConversationSearchResponse> {
    const response = await this.client.get<ConversationSearchResponse>(
      '/api/conversations/search',
      {
        params: options as Record<string, unknown>,
      }
    );
    return response.data;
  }

  async getConversations<TConversation = ConversationInfo>(
    conversationIds: string[]
  ): Promise<Array<TConversation | null>> {
    const response = await this.client.get<Array<TConversation | null>>('/api/conversations', {
      params: { ids: conversationIds },
    });
    return response.data;
  }

  async getConversation<TConversation = ConversationInfo>(
    conversationId: string
  ): Promise<TConversation> {
    const response = await this.client.get<TConversation>(`/api/conversations/${conversationId}`);
    return response.data;
  }

  async sendEvent(
    conversationId: string,
    event: object,
    options: SendConversationEventOptions = {}
  ): Promise<void> {
    await this.client.post(`/api/conversations/${conversationId}/events`, {
      ...event,
      ...(options.run === undefined ? {} : { run: options.run }),
    });
  }

  async pauseConversation(conversationId: string): Promise<Success> {
    const response = await this.client.post<Success>(
      `/api/conversations/${conversationId}/pause`,
      {}
    );
    return response.data;
  }

  async runConversation(conversationId: string): Promise<Success> {
    const response = await this.client.post<Success>(
      `/api/conversations/${conversationId}/run`,
      {}
    );
    return response.data;
  }

  async askAgent(conversationId: string, question: string): Promise<AskAgentResponse> {
    const response = await this.client.post<AskAgentResponse>(
      `/api/conversations/${conversationId}/ask_agent`,
      { question }
    );
    return response.data;
  }

  async getEventCount(conversationId: string): Promise<number> {
    const response = await this.client.get<number>(
      `/api/conversations/${conversationId}/events/count`
    );
    return response.data;
  }

  async respondToConfirmation<TResponse = unknown>(
    conversationId: string,
    request: ConfirmationResponseRequest
  ): Promise<TResponse> {
    const response = await this.client.post<TResponse>(
      `/api/conversations/${conversationId}/events/respond_to_confirmation`,
      request
    );
    return response.data;
  }

  async switchProfile(conversationId: string, profileName: string): Promise<void> {
    await this.client.post<Success>(`/api/conversations/${conversationId}/switch_profile`, {
      profile_name: profileName,
    });
  }

  async deleteConversation(conversationId: string): Promise<void> {
    await this.client.delete<Success>(`/api/conversations/${conversationId}`);
  }

  async updateConversation<TConversation = ConversationInfo>(
    conversationId: string,
    update: UpdateConversationRequest
  ): Promise<TConversation> {
    const response = await this.client.patch<TConversation>(
      `/api/conversations/${conversationId}`,
      update
    );
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
