/**
 * Base conversation interface and types
 *
 * This file defines the abstract interface that all conversation implementations must follow.
 * It mirrors the Python SDK's BaseConversation pattern.
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
import { IWorkspace } from '../workspace/base';

/**
 * Base conversation options that all conversation types share
 */
export interface BaseConversationOptions {
  /** Optional existing conversation ID to resume */
  conversationId?: string;
  /** Callback function for conversation events */
  callback?: ConversationCallbackType;
  /** Initial message to send when starting the conversation */
  initialMessage?: string;
  /** Maximum iterations before stopping (default: 50) */
  maxIterations?: number;
  /** Enable stuck detection (default: true) */
  stuckDetection?: boolean;
}

/**
 * Interface for events list.
 * Provides access to conversation events.
 */
export interface IEventsList {
  /** Add an event to the list */
  addEvent(event: unknown): Promise<void>;
  /** Get all events (may have additional parameters in implementations) */
  getEvents?(): Promise<unknown[]>;
}

/**
 * Interface for conversation state objects.
 * Provides access to conversation events and execution status.
 *
 * Note: This is a minimal interface. Specific implementations (RemoteState, LocalState)
 * may have additional methods and properties.
 */
export interface IConversationState {
  /** The conversation ID */
  readonly id: ConversationID;
  /** Access to the events list */
  readonly events: IEventsList;
}

/**
 * Abstract interface for conversation implementations.
 *
 * Conversations manage the interaction between users and agents, handling message
 * exchange, execution control, and state management. All conversation implementations
 * support the same interface for interoperability.
 *
 * This mirrors the Python SDK's BaseConversation abstract class.
 */
export interface IConversation {
  /** The agent running in the conversation */
  readonly agent: AgentBase;
  /** The workspace for agent operations */
  readonly workspace: IWorkspace;
  /** The conversation ID (available after start()) */
  readonly id: ConversationID;
  /** Access to conversation state */
  readonly state: IConversationState;

  /**
   * Start or resume a conversation.
   *
   * @param options - Start options including initial message
   */
  start(options?: {
    initialMessage?: string;
    maxIterations?: number;
    stuckDetection?: boolean;
  }): Promise<void>;

  /**
   * Get conversation statistics.
   *
   * @returns ConversationStats with iteration count and other metrics
   */
  conversationStats(): Promise<ConversationStats>;

  /**
   * Send a message to the agent.
   *
   * @param message - Either a string (which will be converted to a user message)
   *                  or a Message object
   */
  sendMessage(message: string | Message): Promise<void>;

  /**
   * Execute the agent to process messages and perform actions.
   *
   * This method runs the agent until it finishes processing the current
   * message or reaches the maximum iteration limit.
   */
  run(): Promise<void>;

  /**
   * Pause the agent execution.
   */
  pause(): Promise<void>;

  /**
   * Set the confirmation policy for the conversation.
   *
   * @param policy - The confirmation policy to apply
   */
  setConfirmationPolicy(policy: ConfirmationPolicyBase): Promise<void>;

  /**
   * Send a confirmation response (accept or reject pending actions).
   *
   * @param accept - Whether to accept the pending action
   * @param reason - Optional reason for the decision
   */
  sendConfirmationResponse(accept: boolean, reason?: string): Promise<void>;

  /**
   * Generate a title for the conversation based on message history.
   *
   * @param maxLength - Maximum length of the generated title
   * @param llm - Optional LLM to use for title generation
   * @returns A generated title for the conversation
   */
  generateTitle(maxLength?: number, llm?: LLM): Promise<string>;

  /**
   * Set the title of the conversation.
   *
   * @param title - The title to set
   */
  setTitle(title: string): Promise<void>;

  /**
   * Update secrets available to the agent.
   *
   * @param secrets - Record of secret name to value mappings
   */
  updateSecrets(secrets: Record<string, SecretValue>): Promise<void>;

  /**
   * Start the WebSocket client for real-time event streaming.
   */
  startWebSocketClient(): Promise<void>;

  /**
   * Stop the WebSocket client.
   */
  stopWebSocketClient(): Promise<void>;

  /**
   * Close the conversation and cleanup resources.
   */
  close(): Promise<void>;
}

/**
 * Type discriminator for conversation types.
 * Useful for runtime type checking and factory patterns.
 */
export type ConversationType = 'local' | 'remote';
