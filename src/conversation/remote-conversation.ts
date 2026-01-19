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
  conversationId?: string;
  callback?: ConversationCallbackType;
  initialMessage?: string;
  maxIterations?: number;
  stuckDetection?: boolean;
}

export class RemoteConversation {
  public readonly agent: AgentBase;
  public readonly workspace: RemoteWorkspace;
  private _conversationId?: string;
  private _state?: RemoteState;
  private client: HttpClient;
  private wsClient?: WebSocketCallbackClient;
  private callback?: ConversationCallbackType;

  constructor(
    agent: AgentBase,
    workspace: RemoteWorkspace,
    options: RemoteConversationOptions = {}
  ) {
    this.agent = agent;
    this.workspace = workspace;
    this.callback = options.callback;
    this._conversationId = options.conversationId;

    this.client = new HttpClient({
      baseUrl: workspace.host,
      apiKey: workspace.apiKey,
      timeout: 60000,
    });
  }

  get id(): ConversationID {
    if (!this._conversationId) {
      throw new Error('Conversation ID not set. Call start() to initialize the conversation.');
    }
    return this._conversationId;
  }

  get state(): RemoteState {
    if (!this._state) {
      if (!this._conversationId) {
        throw new Error(
          'Conversation not initialized. Call start() to initialize the conversation.'
        );
      }
      this._state = new RemoteState(this.client, this._conversationId);
    }
    return this._state;
  }

  async start(
    options: { initialMessage?: string; maxIterations?: number; stuckDetection?: boolean } = {}
  ): Promise<void> {
    if (this._conversationId) {
      // Existing conversation - verify it exists
      await this.client.get<ConversationInfo>(`/api/conversations/${this._conversationId}`);
      return;
    }

    // Create new conversation
    let initialMessage: Message | undefined;
    if (options.initialMessage) {
      initialMessage = {
        role: 'user',
        content: [{ type: 'text', text: options.initialMessage }],
      };
    }

    const request: CreateConversationRequest = {
      agent: this.agent,
      initial_message: initialMessage,
      max_iterations: options.maxIterations || 50,
      stuck_detection: options.stuckDetection ?? true,
      workspace: { type: 'local', working_dir: this.workspace.workingDir },
    };

    const response = await this.client.post<ConversationInfo>('/api/conversations', request);
    const conversationInfo = response.data;
    this._conversationId = conversationInfo.id;
  }

  async conversationStats(): Promise<ConversationStats> {
    const response = await this.client.get<ConversationInfo>(`/api/conversations/${this.id}`);
    return response.data.stats;
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

    await this.client.post(`/api/conversations/${this.id}/events`, messageContent);
  }

  async run(): Promise<void> {
    await this.client.post(`/api/conversations/${this.id}/run`);
  }

  async pause(): Promise<void> {
    await this.client.post(`/api/conversations/${this.id}/pause`);
  }

  async setConfirmationPolicy(policy: ConfirmationPolicyBase): Promise<void> {
    await this.client.post(`/api/conversations/${this.id}/confirmation_policy`, policy);
  }

  async sendConfirmationResponse(accept: boolean, reason?: string): Promise<void> {
    const request: ConfirmationResponseRequest = { accept, reason };
    await this.client.post(`/api/conversations/${this.id}/events/respond_to_confirmation`, request);
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
    // Convert SecretValue functions to StaticSecret objects
    const secretObjects: Record<string, { kind: 'StaticSecret'; value: string }> = {};
    for (const [key, value] of Object.entries(secrets)) {
      secretObjects[key] = {
        kind: 'StaticSecret',
        value: typeof value === 'function' ? value() : value,
      };
    }

    const request: UpdateSecretsRequest = { secrets: secretObjects };
    await this.client.post(`/api/conversations/${this.id}/secrets`, request);
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
      host: this.workspace.host,
      conversationId: this.id,
      callback: combinedCallback,
      apiKey: this.workspace.apiKey,
    });

    this.wsClient.start();
  }

  async stopWebSocketClient(): Promise<void> {
    if (this.wsClient) {
      this.wsClient.stop();
      this.wsClient = undefined;
    }
  }

  async close(): Promise<void> {
    await this.stopWebSocketClient();
    this.client.close();
    this.workspace.close();
  }
}
