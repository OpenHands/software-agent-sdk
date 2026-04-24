/**
 * Conversation manager for handling multiple conversations
 */

import { HttpClient } from '../client/http-client';
import { DesktopClient } from '../client/desktop-client';
import { LLMMetadataClient } from '../client/llm-client';
import { ServerClient } from '../client/server-client';
import { SettingsClient } from '../client/settings-client';
import { SkillsClient } from '../client/skills-client';
import { ToolClient } from '../client/tool-client';
import { VSCodeClient } from '../client/vscode-client';
import { RemoteConversation } from './remote-conversation';
import { RemoteWorkspace } from '../workspace/remote-workspace';
import {
  ACPAgentConfig,
  ACPConversationInfo,
  ACPConversationSearchResponse,
  ConversationInfo,
  ConversationSearchRequest,
  ConversationSearchResponse,
  CreateACPConversationRequest,
  UpdateConversationRequest,
} from '../models/conversation';
import { AgentBase, ConversationExecutionStatus, ConversationID, Success } from '../types/base';

export class ACPConversationNamespace {
  constructor(private readonly manager: ConversationManager) {}

  searchConversations(
    options: ConversationSearchRequest = {}
  ): Promise<ACPConversationSearchResponse> {
    return this.manager.searchACPConversations(options);
  }

  countConversations(options: { status?: ConversationExecutionStatus } = {}): Promise<number> {
    return this.manager.countACPConversations(options);
  }

  getConversations(conversationIds: ConversationID[]): Promise<Array<ACPConversationInfo | null>> {
    return this.manager.getACPConversations(conversationIds);
  }

  getAllConversations(): Promise<ACPConversationInfo[]> {
    return this.manager.getAllACPConversations();
  }

  getConversation(conversationId: ConversationID): Promise<ACPConversationInfo> {
    return this.manager.getACPConversation(conversationId);
  }

  createConversation(
    agent: ACPAgentConfig,
    options: {
      initialMessage?: string;
      maxIterations?: number;
      stuckDetection?: boolean;
      workingDir?: string;
    } = {}
  ): Promise<ACPConversationInfo> {
    return this.manager.createACPConversation(agent, options);
  }
}

export interface ConversationManagerOptions {
  host: string;
  apiKey?: string;
}

export class ConversationManager {
  private readonly client: HttpClient;
  public readonly host: string;
  public readonly apiKey?: string;
  public readonly server: ServerClient;
  public readonly llm: LLMMetadataClient;
  public readonly settings: SettingsClient;
  public readonly skills: SkillsClient;
  public readonly tools: ToolClient;
  public readonly vscode: VSCodeClient;
  public readonly desktop: DesktopClient;
  public readonly acp: ACPConversationNamespace;

  constructor(options: ConversationManagerOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;

    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: 60000,
    });

    const clientOptions = {
      host: this.host,
      ...(this.apiKey ? { apiKey: this.apiKey } : {}),
    };

    this.server = new ServerClient(clientOptions);
    this.llm = new LLMMetadataClient(clientOptions);
    this.settings = new SettingsClient(clientOptions);
    this.skills = new SkillsClient(clientOptions);
    this.tools = new ToolClient(clientOptions);
    this.vscode = new VSCodeClient(clientOptions);
    this.desktop = new DesktopClient(clientOptions);
    this.acp = new ACPConversationNamespace(this);
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
        params: options as Record<string, unknown>,
      }
    );
    return response.data;
  }

  /**
   * Count conversations matching the provided filters.
   */
  async countConversations(
    options: { status?: ConversationExecutionStatus } = {}
  ): Promise<number> {
    const response = await this.client.get<number>('/api/conversations/count', {
      params: options as Record<string, unknown>,
    });
    return response.data;
  }

  /**
   * Batch get conversations by ID.
   */
  async getConversations(
    conversationIds: ConversationID[]
  ): Promise<Array<ConversationInfo | null>> {
    const response = await this.client.get<Array<ConversationInfo | null>>('/api/conversations', {
      params: { ids: conversationIds },
    });
    return response.data;
  }

  /**
   * Get all conversations (convenience method)
   */
  async getAllConversations(options?: { tag?: string[] }): Promise<ConversationInfo[]> {
    const conversations: ConversationInfo[] = [];
    let nextPageId: string | undefined;

    do {
      const response = await this.searchConversations({
        page_id: nextPageId,
        limit: 100,
        ...(options?.tag ? { tag: options.tag } : {}),
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
  async loadConversation(
    conversationId: ConversationID,
    workingDir: string = '/tmp'
  ): Promise<RemoteConversation> {
    const conversationInfo = await this.getConversation(conversationId);

    const workspace = new RemoteWorkspace({
      host: this.host,
      workingDir,
      apiKey: this.apiKey,
    });

    const conversation = new RemoteConversation(conversationInfo.agent, workspace, {
      conversationId,
    });

    await conversation.start();
    return conversation;
  }

  /**
   * Search ACP-capable conversations.
   */
  async searchACPConversations(
    options: ConversationSearchRequest = {}
  ): Promise<ACPConversationSearchResponse> {
    const response = await this.client.get<ACPConversationSearchResponse>(
      '/api/acp/conversations/search',
      {
        params: options as Record<string, unknown>,
      }
    );
    return response.data;
  }

  /**
   * Count ACP-capable conversations.
   */
  async countACPConversations(
    options: { status?: ConversationExecutionStatus } = {}
  ): Promise<number> {
    const response = await this.client.get<number>('/api/acp/conversations/count', {
      params: options,
    });
    return response.data;
  }

  /**
   * Batch get ACP-capable conversations by ID.
   */
  async getACPConversations(
    conversationIds: ConversationID[]
  ): Promise<Array<ACPConversationInfo | null>> {
    const response = await this.client.get<Array<ACPConversationInfo | null>>(
      '/api/acp/conversations',
      {
        params: { ids: conversationIds },
      }
    );
    return response.data;
  }

  /**
   * Get all ACP-capable conversations (convenience method).
   */
  async getAllACPConversations(): Promise<ACPConversationInfo[]> {
    const conversations: ACPConversationInfo[] = [];
    let nextPageId: string | undefined;

    do {
      const response = await this.searchACPConversations({
        page_id: nextPageId,
        limit: 100,
      });

      conversations.push(...response.items);
      nextPageId = response.next_page_id;
    } while (nextPageId);

    return conversations;
  }

  /**
   * Get a specific ACP-capable conversation by ID.
   */
  async getACPConversation(conversationId: ConversationID): Promise<ACPConversationInfo> {
    const response = await this.client.get<ACPConversationInfo>(
      `/api/acp/conversations/${conversationId}`
    );
    return response.data;
  }

  /**
   * Create a new ACP-capable conversation.
   */
  async createACPConversation(
    agent: ACPAgentConfig,
    options: {
      initialMessage?: string;
      maxIterations?: number;
      stuckDetection?: boolean;
      workingDir?: string;
    } = {}
  ): Promise<ACPConversationInfo> {
    let initialMessage: CreateACPConversationRequest['initial_message'];
    if (options.initialMessage) {
      initialMessage = {
        role: 'user' as const,
        content: [{ type: 'text' as const, text: options.initialMessage }],
      };
    }

    const request: CreateACPConversationRequest = {
      agent,
      initial_message: initialMessage,
      max_iterations: options.maxIterations || 500,
      stuck_detection: options.stuckDetection ?? true,
      workspace: { type: 'local', working_dir: options.workingDir || '/tmp' },
    };

    const response = await this.client.post<ACPConversationInfo>('/api/acp/conversations', request);
    return response.data;
  }

  /**
   * Delete a conversation
   */
  async deleteConversation(conversationId: ConversationID): Promise<void> {
    await this.client.delete<Success>(`/api/conversations/${conversationId}`);
  }

  /**
   * Update conversation metadata (e.g. title)
   */
  async updateConversation(
    conversationId: ConversationID,
    update: UpdateConversationRequest
  ): Promise<ConversationInfo> {
    const response = await this.client.patch<ConversationInfo>(
      `/api/conversations/${conversationId}`,
      update
    );
    return response.data;
  }

  /**
   * Close the manager and cleanup resources
   */
  close(): void {
    this.server.close();
    this.llm.close();
    this.settings.close();
    this.skills.close();
    this.tools.close();
    this.vscode.close();
    this.desktop.close();
    this.client.close();
  }
}
