/**
 * Remote conversation implementation
 */

// import { v4 as uuidv4 } from 'uuid'; // Unused for now
import { HttpClient } from '../client/http-client';
import { WebSocketCallbackClient } from '../events/websocket-client';
import { RemoteState } from './remote-state';
import { RemoteWorkspace } from '../workspace/remote-workspace';
import {
  ConversationID,
  Message,
  ConversationCallbackType,
  ConfirmationPolicyBase,
  ConversationStats,
  AgentBase,
  SecretValue,
} from '../types/base';
import {
  ConversationInfo,
  SendMessageRequest,
  ConfirmationResponseRequest,
  CreateConversationRequest,
  GenerateTitleRequest,
  GenerateTitleResponse,
  UpdateSecretsRequest,
} from '../models/conversation';

export interface RemoteConversationOptions {
  host: string;
  conversationId?: string;
  apiKey?: string;
  callback?: ConversationCallbackType;
}

export class RemoteConversation {
  public readonly host: string;
  public readonly apiKey?: string;
  private _conversationId?: string;
  private _state?: RemoteState;
  private _workspace?: RemoteWorkspace;
  private client: HttpClient;
  private wsClient?: WebSocketCallbackClient;
  private callback?: ConversationCallbackType;

  constructor(options: RemoteConversationOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this._conversationId = options.conversationId;
    this.callback = options.callback;

    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: 60000,
    });
  }

  get id(): ConversationID {
    if (!this._conversationId) {
      throw new Error('Conversation ID not set. Create or load a conversation first.');
    }
    return this._conversationId;
  }

  get state(): RemoteState {
    if (!this._state) {
      if (!this._conversationId) {
        throw new Error('Conversation not initialized. Create or load a conversation first.');
      }
      this._state = new RemoteState(this.client, this._conversationId);
    }
    return this._state;
  }

  get workspace(): RemoteWorkspace {
    if (!this._workspace) {
      throw new Error('Workspace not initialized. Create or load a conversation first.');
    }
    return this._workspace;
  }

  async conversationStats(): Promise<ConversationStats> {
    const response = await this.client.get<ConversationStats>(
      `/api/conversations/${this.id}/stats`
    );
    return response.data;
  }

  async sendMessage(message: string | Message): Promise<void> {
    let messageContent: SendMessageRequest;

    if (typeof message === 'string') {
      messageContent = {
        role: 'user',
        content: [{ type: 'text', text: message }],
        run: false,
      };
    } else {
      messageContent = {
        role: 'user',
        content: message.content,
        run: false,
      };
    }

    await this.client.post(`/api/conversations/${this.id}/send_message`, messageContent);
  }

  async run(): Promise<void> {
    await this.client.post(`/api/conversations/${this.id}/run`);
  }

  async pause(): Promise<void> {
    await this.client.post(`/api/conversations/${this.id}/pause`);
  }

  async setConfirmationPolicy(policy: ConfirmationPolicyBase): Promise<void> {
    await this.client.post(`/api/conversations/${this.id}/set_confirmation_policy`, policy);
  }

  async sendConfirmationResponse(accept: boolean, reason?: string): Promise<void> {
    const request: ConfirmationResponseRequest = { accept, reason };
    await this.client.post(`/api/conversations/${this.id}/send_confirmation_response`, request);
  }

  async generateTitle(maxLength: number = 50, llm?: any): Promise<string> {
    const request: GenerateTitleRequest = { max_length: maxLength };
    if (llm) {
      request.llm = llm;
    }

    const response = await this.client.post<GenerateTitleResponse>(
      `/api/conversations/${this.id}/generate_title`,
      request
    );
    return response.data.title;
  }

  async updateSecrets(secrets: Record<string, SecretValue>): Promise<void> {
    // Convert SecretValue functions to strings
    const secretStrings: Record<string, string> = {};
    for (const [key, value] of Object.entries(secrets)) {
      secretStrings[key] = typeof value === 'function' ? value() : value;
    }

    const request: UpdateSecretsRequest = { secrets: secretStrings };
    await this.client.post(`/api/conversations/${this.id}/update_secrets`, request);
  }

  async startWebSocketClient(): Promise<void> {
    if (this.wsClient) {
      return;
    }

    // Create combined callback that handles both user callback and state updates
    const combinedCallback: ConversationCallbackType = (event) => {
      // Add event to the events list
      this.state.events.addEvent(event).catch((error) => {
        console.error('Error adding event to events list:', error);
      });

      // Update state if it's a state update event
      const stateCallback = this.state.createStateUpdateCallback();
      stateCallback(event);

      // Call user callback if provided
      if (this.callback) {
        this.callback(event);
      }
    };

    this.wsClient = new WebSocketCallbackClient({
      host: this.host,
      conversationId: this.id,
      callback: combinedCallback,
      apiKey: this.apiKey,
    });

    this.wsClient.start();
  }

  async stopWebSocketClient(): Promise<void> {
    if (this.wsClient) {
      this.wsClient.stop();
      this.wsClient = undefined;
    }
  }

  // Static factory methods
  static async create(
    host: string,
    agent: AgentBase,
    options: {
      apiKey?: string;
      initialMessage?: string;
      maxIterations?: number;
      stuckDetection?: boolean;
      workspace?: any;
      callback?: ConversationCallbackType;
    } = {}
  ): Promise<RemoteConversation> {
    const client = new HttpClient({
      baseUrl: host.replace(/\/$/, ''),
      apiKey: options.apiKey,
      timeout: 60000,
    });

    const request: CreateConversationRequest = {
      agent,
      initial_message: options.initialMessage,
      max_iterations: options.maxIterations || 50,
      stuck_detection: options.stuckDetection ?? true,
      workspace: options.workspace || { type: 'local', working_dir: '/tmp' },
    };

    const response = await client.post<ConversationInfo>('/api/conversations', request);
    const conversationInfo = response.data;

    const conversation = new RemoteConversation({
      host,
      conversationId: conversationInfo.id,
      apiKey: options.apiKey,
      callback: options.callback,
    });

    // Initialize workspace
    conversation._workspace = new RemoteWorkspace({
      host,
      workingDir: conversationInfo.workspace?.working_dir || '/tmp',
      apiKey: options.apiKey,
    });

    return conversation;
  }

  static async load(
    host: string,
    conversationId: string,
    options: {
      apiKey?: string;
      callback?: ConversationCallbackType;
    } = {}
  ): Promise<RemoteConversation> {
    const conversation = new RemoteConversation({
      host,
      conversationId,
      apiKey: options.apiKey,
      callback: options.callback,
    });

    // Verify conversation exists and get workspace info
    const response = await conversation.client.get<ConversationInfo>(
      `/api/conversations/${conversationId}`
    );
    const conversationInfo = response.data;

    // Initialize workspace
    conversation._workspace = new RemoteWorkspace({
      host,
      workingDir: conversationInfo.workspace?.working_dir || '/tmp',
      apiKey: options.apiKey,
    });

    return conversation;
  }

  async close(): Promise<void> {
    await this.stopWebSocketClient();
    this.client.close();
    if (this._workspace) {
      this._workspace.close();
    }
  }
}
