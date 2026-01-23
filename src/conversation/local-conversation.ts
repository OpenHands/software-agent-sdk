/**
 * Local conversation implementation
 *
 * This implements the IConversation interface for local execution. Unlike RemoteConversation,
 * LocalConversation runs the agent loop locally without connecting to a remote server.
 *
 * This mirrors the Python SDK's LocalConversation class.
 *
 * NOTE: This is a stub implementation. The actual implementation will need to:
 * - Run the agent loop locally
 * - Manage conversation state in memory
 * - Handle tool execution through LocalWorkspace
 */

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
import { LocalWorkspace } from '../workspace/local-workspace';
import {
  IConversation,
  IConversationState,
  IEventsList,
  BaseConversationOptions,
} from './base';

/**
 * Options for creating a LocalConversation instance.
 */
export interface LocalConversationOptions extends BaseConversationOptions {
  /** Optional persistence directory for saving conversation state */
  persistenceDir?: string;
}

/**
 * Stub implementation of events list for local conversations.
 */
class LocalEventsList implements IEventsList {
  private events: unknown[] = [];

  async addEvent(event: unknown): Promise<void> {
    this.events.push(event);
  }

  async getEvents(): Promise<unknown[]> {
    return [...this.events];
  }
}

/**
 * Stub implementation of conversation state for local conversations.
 */
class LocalConversationState implements IConversationState {
  readonly id: ConversationID;
  readonly events: IEventsList;
  executionStatus: string = 'idle';
  confirmationPolicy?: ConfirmationPolicyBase;

  constructor(id: ConversationID) {
    this.id = id;
    this.events = new LocalEventsList();
  }
}

/**
 * Local conversation implementation that runs the agent loop locally.
 *
 * LocalConversation provides direct agent execution on the local system without
 * requiring a remote server. It's suitable for development, testing, and scenarios
 * where the agent should run in the same process.
 *
 * NOTE: This is a stub implementation. Full implementation requires:
 * - Agent loop execution
 * - LLM integration
 * - Tool execution coordination with LocalWorkspace
 *
 * Example:
 * ```typescript
 * const workspace = new LocalWorkspace({ workingDir: '/path/to/project' });
 * const conversation = new LocalConversation(agent, workspace, {
 *   maxIterations: 50,
 *   persistenceDir: '/path/to/persistence'
 * });
 * await conversation.start({ initialMessage: 'Hello!' });
 * await conversation.run();
 * await conversation.close();
 * ```
 */
export class LocalConversation implements IConversation {
  public readonly agent: AgentBase;
  public readonly workspace: LocalWorkspace;
  private _conversationId?: string;
  private _state?: LocalConversationState;
  private callback?: ConversationCallbackType;
  private persistenceDir?: string;

  constructor(
    agent: AgentBase,
    workspace: LocalWorkspace,
    options: LocalConversationOptions = {}
  ) {
    this.agent = agent;
    this.workspace = workspace;
    this.callback = options.callback;
    this._conversationId = options.conversationId;
    this.persistenceDir = options.persistenceDir;
  }

  get id(): ConversationID {
    if (!this._conversationId) {
      throw new Error('Conversation ID not set. Call start() to initialize the conversation.');
    }
    return this._conversationId;
  }

  get state(): IConversationState {
    if (!this._state) {
      if (!this._conversationId) {
        throw new Error(
          'Conversation not initialized. Call start() to initialize the conversation.'
        );
      }
      this._state = new LocalConversationState(this._conversationId);
    }
    return this._state;
  }

  /**
   * Start or resume a conversation.
   *
   * STUB: This method needs to be implemented to:
   * - Generate or validate conversation ID
   * - Initialize conversation state
   * - Set up agent context
   */
  async start(
    options: { initialMessage?: string; maxIterations?: number; stuckDetection?: boolean } = {}
  ): Promise<void> {
    // Generate a conversation ID if not provided
    if (!this._conversationId) {
      this._conversationId = this.generateConversationId();
    }

    // Initialize state
    this._state = new LocalConversationState(this._conversationId);

    // TODO: Implement full initialization
    // - Set up agent context
    // - Load persisted state if available
    // - Process initial message if provided

    if (options.initialMessage) {
      // TODO: Add initial message to events
      console.debug(`LocalConversation: Would process initial message: ${options.initialMessage}`);
    }

    console.debug(`LocalConversation started with ID: ${this._conversationId}`);
  }

  /**
   * Get conversation statistics.
   *
   * STUB: Returns placeholder stats.
   */
  async conversationStats(): Promise<ConversationStats> {
    // TODO: Implement actual stats tracking
    return {
      total_events: 0,
      message_events: 0,
      action_events: 0,
      observation_events: 0,
    };
  }

  /**
   * Send a message to the agent.
   *
   * STUB: This method needs to be implemented to add the message to the conversation.
   */
  async sendMessage(message: string | Message): Promise<void> {
    // TODO: Implement message handling
    throw new Error(
      'LocalConversation.sendMessage is not yet implemented. ' +
      `Message: ${typeof message === 'string' ? message : JSON.stringify(message)}`
    );
  }

  /**
   * Execute the agent to process messages.
   *
   * STUB: This method needs to be implemented to run the agent loop.
   */
  async run(): Promise<void> {
    // TODO: Implement agent loop execution
    throw new Error(
      'LocalConversation.run is not yet implemented. ' +
      'This requires agent loop implementation with LLM integration.'
    );
  }

  /**
   * Pause agent execution.
   *
   * STUB: This method needs to be implemented to pause the agent loop.
   */
  async pause(): Promise<void> {
    // TODO: Implement pause functionality
    if (this._state) {
      this._state.executionStatus = 'paused';
    }
    console.debug('LocalConversation: pause() called');
  }

  /**
   * Set the confirmation policy.
   *
   * STUB: Stores the policy for future use.
   */
  async setConfirmationPolicy(policy: ConfirmationPolicyBase): Promise<void> {
    if (this._state) {
      this._state.confirmationPolicy = policy;
    }
    console.debug('LocalConversation: confirmation policy set');
  }

  /**
   * Send a confirmation response.
   *
   * STUB: This method needs to be implemented.
   */
  async sendConfirmationResponse(accept: boolean, reason?: string): Promise<void> {
    // TODO: Implement confirmation response handling
    throw new Error(
      'LocalConversation.sendConfirmationResponse is not yet implemented. ' +
      `Accept: ${accept}, Reason: ${reason}`
    );
  }

  /**
   * Generate a title for the conversation.
   *
   * STUB: This method needs LLM integration.
   */
  async generateTitle(_maxLength: number = 50, _llm?: LLM): Promise<string> {
    // TODO: Implement title generation using LLM
    throw new Error(
      'LocalConversation.generateTitle is not yet implemented. ' +
      'This requires LLM integration.'
    );
  }

  /**
   * Update secrets available to the agent.
   *
   * STUB: Stores secrets for future use.
   */
  async updateSecrets(secrets: Record<string, SecretValue>): Promise<void> {
    // TODO: Implement secrets storage
    console.debug(`LocalConversation: updateSecrets() called with ${Object.keys(secrets).length} secrets`);
  }

  /**
   * Start WebSocket client.
   *
   * NOTE: LocalConversation doesn't use WebSocket since it runs locally.
   * Events are delivered directly through the callback.
   */
  async startWebSocketClient(): Promise<void> {
    // No-op for local conversation - events are delivered directly
    console.debug('LocalConversation: startWebSocketClient() is a no-op for local conversations');
  }

  /**
   * Stop WebSocket client.
   *
   * NOTE: LocalConversation doesn't use WebSocket.
   */
  async stopWebSocketClient(): Promise<void> {
    // No-op for local conversation
    console.debug('LocalConversation: stopWebSocketClient() is a no-op for local conversations');
  }

  /**
   * Close the conversation and cleanup resources.
   */
  async close(): Promise<void> {
    // TODO: Implement proper cleanup
    // - Save state if persistence is enabled
    // - Clean up any running processes
    this.workspace.close();
    console.debug('LocalConversation: closed');
  }

  /**
   * Generate a unique conversation ID.
   */
  private generateConversationId(): string {
    // Simple UUID v4 implementation
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      const v = c === 'x' ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }
}
