/**
 * Conversation manager for handling multiple conversations
 */

import { HttpClient } from '../client/http-client';
import { RemoteConversation } from './remote-conversation';
import { RemoteWorkspace } from '../workspace/remote-workspace';
import {
  ConversationInfo,
  ConversationSearchRequest,
  ConversationSearchResponse,
} from '../models/conversation';
import { AgentBase, ConversationID, Success } from '../types/base';

export interface ConversationManagerOptions {
  host: string;
  apiKey?: string;
}

export class ConversationManager {
  private client: HttpClient;
  public readonly host: string;
  public readonly apiKey?: string;

  constructor(options: ConversationManagerOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;

    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: 60000,
    });
  }

  /**
   * Search/list conversations
   */
  async searchConversations(
    options: ConversationSearchRequest = {}
  ): Promise<ConversationSearchResponse> {
    const response = await this.client.get<ConversationSearchResponse>(
      '/api/conversations/search',
      {
        params: options,
      }
    );
    return response.data;
  }

  /**
   * Get all conversations (convenience method)
   */
  async getAllConversations(): Promise<ConversationInfo[]> {
    const conversations: ConversationInfo[] = [];
    let nextPageId: string | undefined;

    do {
      const response = await this.searchConversations({
        page_id: nextPageId,
        limit: 100,
      });

      conversations.push(...response.items);
      nextPageId = response.next_page_id;
    } while (nextPageId);

    return conversations;
  }

  /**
   * Get a specific conversation by ID
   */
  async getConversation(conversationId: ConversationID): Promise<ConversationInfo> {
    const response = await this.client.get<ConversationInfo>(
      `/api/conversations/${conversationId}`
    );
    return response.data;
  }

  /**
   * Create a new conversation
   */
  async createConversation(
    agent: AgentBase,
    options: {
      initialMessage?: string;
      maxIterations?: number;
      stuckDetection?: boolean;
      workingDir?: string;
    } = {}
  ): Promise<RemoteConversation> {
    // Create a workspace for the conversation
    const workspace = new RemoteWorkspace({
      host: this.host,
      workingDir: options.workingDir || '/tmp',
      apiKey: this.apiKey,
    });

    const conversation = new RemoteConversation(agent, workspace, {
      maxIterations: options.maxIterations,
      stuckDetection: options.stuckDetection,
    });

    await conversation.start({
      initialMessage: options.initialMessage,
    });

    return conversation;
  }

  /**
   * Load an existing conversation
   */
  async loadConversation(conversationId: ConversationID, workingDir: string = '/tmp'): Promise<RemoteConversation> {
    // Get conversation info to extract the agent
    const conversationInfo = await this.getConversation(conversationId);

    // Create a workspace for the existing conversation
    const workspace = new RemoteWorkspace({
      host: this.host,
      workingDir,
      apiKey: this.apiKey,
    });

    const conversation = new RemoteConversation(conversationInfo.agent, workspace, {
      conversationId: conversationId,
    });

    await conversation.start();
    return conversation;
  }

  /**
   * Delete a conversation
   */
  async deleteConversation(conversationId: ConversationID): Promise<void> {
    await this.client.delete<Success>(`/api/conversations/${conversationId}`);
  }

  /**
   * Close the manager and cleanup resources
   */
  close(): void {
    this.client.close();
  }
}
