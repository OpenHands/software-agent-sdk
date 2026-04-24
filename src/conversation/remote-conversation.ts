/**
 * Remote conversation implementation
 *
 * This implements the IConversation interface by connecting to a remote OpenHands
 * agent server. It mirrors the Python SDK's RemoteConversation class.
 */

import { HttpClient } from '../client/http-client';
import { WebSocketCallbackClient, ErrorCallbackType } from '../events/websocket-client';
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
  LLM,
} from '../types/base';
import {
  ConversationInfo,
  SendMessageRequest,
  ConfirmationResponseRequest,
  CreateConversationRequest,
  GenerateTitleRequest,
  GenerateTitleResponse,
  UpdateConversationRequest,
  UpdateSecretsRequest,
  AskAgentRequest,
  AskAgentResponse,
  SetSecurityAnalyzerRequest,
  AgentResponseResult,
  ForkConversationRequest,
} from '../models/conversation';
import { IConversation, BaseConversationOptions } from './base';
import { Success } from '../types/base';
import type { HookConfig } from '../hooks';

/**
 * Options for creating a RemoteConversation instance.
 */
export interface RemoteConversationOptions extends BaseConversationOptions {
  /**
   * Optional hook configuration for this conversation.
   * Hooks are shell scripts that run server-side at key lifecycle events
   * (PreToolUse, PostToolUse, UserPromptSubmit, Stop, etc.).
   */
  hookConfig?: HookConfig;
  /**
   * Optional error callback for non-fatal errors (WebSocket issues, state update failures).
   * If not provided, these errors are silently ignored.
   */
  onError?: ErrorCallbackType;
}

/**
 * Remote conversation implementation that connects to an OpenHands agent server.
 *
 * RemoteConversation provides access to a conversation running on a remote
 * OpenHands agent server. This is the recommended approach for production deployments
 * as it provides better isolation and security.
 *
 * Example:
 * ```typescript
 * const workspace = new RemoteWorkspace({
 *   host: 'https://agent-server.example.com',
 *   workingDir: '/workspace',
 *   apiKey: 'your-api-key'
 * });
 * const conversation = new RemoteConversation(agent, workspace, {
 *   callback: (event) => console.log(event)
 * });
 * await conversation.start({ initialMessage: 'Hello!' });
 * await conversation.run();
 * await conversation.close();
 * ```
 */
export class RemoteConversation implements IConversation {
  public readonly agent: AgentBase;
  public readonly workspace: RemoteWorkspace;
  private _conversationId?: string;
  private _state?: RemoteState;
  private client: HttpClient;
  private wsClient?: WebSocketCallbackClient;
  private callback?: ConversationCallbackType;
  private onError?: ErrorCallbackType;
  private hookConfig?: HookConfig;

  constructor(
    agent: AgentBase,
    workspace: RemoteWorkspace,
    options: RemoteConversationOptions = {}
  ) {
    this.agent = agent;
    this.workspace = workspace;
    this.callback = options.callback;
    this.onError = options.onError;
    this._conversationId = options.conversationId;
    this.hookConfig = options.hookConfig;

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
    options: {
      initialMessage?: string;
      maxIterations?: number;
      stuckDetection?: boolean;
      hookConfig?: HookConfig;
    } = {}
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

    // Use hook config from start options, falling back to constructor option
    const hookConfig = options.hookConfig ?? this.hookConfig ?? undefined;

    const request: CreateConversationRequest = {
      agent: this.agent,
      initial_message: initialMessage,
      max_iterations: options.maxIterations || 500,
      stuck_detection: options.stuckDetection ?? true,
      workspace: { type: 'local', working_dir: this.workspace.workingDir },
      hook_config: hookConfig ?? null,
    };

    const response = await this.client.post<ConversationInfo>('/api/conversations', request);
    const conversationInfo = response.data;
    this._conversationId = conversationInfo.id;
  }

  /**
   * Load hooks configuration from the server workspace.
   *
   * This calls the server's hooks endpoint to read `.openhands/hooks.json`
   * from the project directory.
   *
   * @param projectDir - Optional project directory path. Defaults to the workspace working dir.
   * @returns The hook configuration, or null if no hooks are configured.
   */
  async loadHooks(projectDir?: string): Promise<HookConfig | null> {
    const response = await this.client.post<{ hook_config: HookConfig | null }>('/api/hooks', {
      project_dir: projectDir ?? this.workspace.workingDir,
    });
    return response.data.hook_config;
  }

  /**
   * Get the hook configuration for this conversation from the server.
   *
   * Fetches the current conversation info and returns the hook_config field.
   *
   * @returns The hook configuration, or null if no hooks are configured.
   */
  async getHookConfig(): Promise<HookConfig | null> {
    const response = await this.client.get<ConversationInfo>(`/api/conversations/${this.id}`);
    return response.data.hook_config ?? null;
  }

  async conversationStats(): Promise<ConversationStats> {
    const response = await this.client.get<ConversationInfo>(`/api/conversations/${this.id}`);
    const stats = response.data.stats ?? response.data.conversation_stats;
    if (!stats) {
      throw new Error('No conversation stats available');
    }
    return stats;
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

  async setTitle(title: string): Promise<void> {
    const request: UpdateConversationRequest = { title };
    await this.client.patch(`/api/conversations/${this.id}`, request);
  }

  async generateTitle(maxLength: number = 50, llm?: LLM): Promise<string> {
    const request: GenerateTitleRequest = { max_length: maxLength };
    if (llm) {
      request.llm = llm;
    }

    const response = await this.client.post<GenerateTitleResponse>(
      `/api/conversations/${this.id}/generate_title`,
      request
    );
    const title = response.data.title;
    await this.setTitle(title);
    return title;
  }

  /**
   * Ask the agent a simple question without affecting conversation state.
   * This is useful for getting quick answers or clarifications.
   */
  async askAgent(question: string): Promise<string> {
    const request: AskAgentRequest = { question };
    const response = await this.client.post<AskAgentResponse>(
      `/api/conversations/${this.id}/ask_agent`,
      request
    );
    return response.data.response;
  }

  /**
   * Get the agent's final response text for this conversation.
   */
  async getAgentFinalResponse(): Promise<string> {
    const response = await this.client.get<AgentResponseResult>(
      `/api/conversations/${this.id}/agent_final_response`
    );
    return response.data.response;
  }

  /**
   * Switch the conversation to a named LLM profile.
   */
  async switchProfile(profileName: string): Promise<void> {
    await this.client.post(`/api/conversations/${this.id}/switch_profile`, {
      profile_name: profileName,
    });
  }

  /**
   * Fork the current conversation and return a new RemoteConversation instance.
   */
  async fork(request: ForkConversationRequest = {}): Promise<RemoteConversation> {
    const response = await this.client.post<ConversationInfo>(
      `/api/conversations/${this.id}/fork`,
      request
    );

    const forkWorkspace = new RemoteWorkspace({
      host: this.workspace.host,
      workingDir: this.workspace.workingDir,
      apiKey: this.workspace.apiKey,
    });

    return new RemoteConversation(response.data.agent, forkWorkspace, {
      conversationId: response.data.id,
      callback: this.callback,
      onError: this.onError,
      hookConfig: response.data.hook_config ?? this.hookConfig,
    });
  }

  /**
   * Download the persisted conversation trajectory as a ZIP blob.
   */
  async downloadTrajectory(): Promise<Blob> {
    const response = await this.client.get<Blob>(`/api/file/download-trajectory/${this.id}`, {
      responseType: 'blob',
    });
    return response.data;
  }

  /**
   * Force condensation of the conversation history.
   * This can help reduce memory usage for long conversations.
   */
  async condense(): Promise<void> {
    await this.client.post<Success>(`/api/conversations/${this.id}/condense`);
  }

  /**
   * Set the security analyzer for the conversation.
   * The security analyzer evaluates action risks.
   */
  async setSecurityAnalyzer(securityAnalyzer: any | null): Promise<void> {
    const request: SetSecurityAnalyzerRequest = { security_analyzer: securityAnalyzer };
    await this.client.post(`/api/conversations/${this.id}/security_analyzer`, request);
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

    const reportError = (error: Error): void => {
      if (this.onError) {
        this.onError(error);
      }
    };

    // Create combined callback that handles both user callback and state updates
    const combinedCallback: ConversationCallbackType = (event) => {
      // Add event to the events list
      this.state.events.addEvent(event).catch((error) => {
        reportError(
          error instanceof Error ? error : new Error(`Error adding event to events list: ${error}`)
        );
      });

      // Update state if it's a state update event
      const stateCallback = this.state.createStateUpdateCallback(this.onError);
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
      onError: this.onError,
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
