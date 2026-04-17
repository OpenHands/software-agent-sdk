/**
 * Local conversation implementation
 *
 * This implements the IConversation interface for local execution. Unlike RemoteConversation,
 * LocalConversation runs the agent loop locally without connecting to a remote server.
 *
 * This mirrors the Python SDK's LocalConversation class.
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
  Event,
} from '../types/base';
import { LocalWorkspace } from '../workspace/local-workspace';
import { IWorkspace } from '../workspace/base';
import { IConversation, IConversationState, IEventsList, BaseConversationOptions } from './base';
import { ILLM, ChatMessage, Tool, ToolCall, TokenStreamEvent } from '../llm/base';
import { generateSystemPrompt, TOOL_DESCRIPTIONS } from '../prompts';
import { SecretRegistry } from './secret-registry';
import { StuckDetector, StuckDetectionThresholds, StuckDetectionResult } from './stuck-detector';
import {
  BaseEvent,
  ActionEvent,
  UserRejectObservation,
  ConversationStateUpdateEvent,
  generateEventId,
} from '../events/types';
import { ConfirmationPolicy, NeverConfirm } from '../security/confirmation-policy';
import { SecurityAnalyzer } from '../security/security-analyzer';

/**
 * Tool executor function type.
 * Takes a tool call and returns the result string.
 */
export type ToolExecutor = (toolCall: ToolCall) => Promise<string> | string;

/**
 * Token callback type for the conversation level.
 */
export type ConversationTokenCallback = (event: TokenStreamEvent) => void;

/**
 * Options for creating a LocalConversation instance.
 */
export interface LocalConversationOptions extends BaseConversationOptions {
  /** The LLM instance to use for the conversation */
  llm: ILLM;
  /** Optional system prompt for the agent */
  systemPrompt?: string;
  /** Optional persistence directory for saving conversation state */
  persistenceDir?: string;
  /** Custom tools to provide to the LLM (in addition to or instead of built-in tools) */
  tools?: Tool[];
  /** Custom tool executor function. If provided, handles all tool calls. */
  toolExecutor?: ToolExecutor;
  /** Whether to include built-in tools (execute_command, read_file, etc.). Default: true if no custom tools provided */
  includeBuiltinTools?: boolean;
  /** Token callback for streaming tokens during LLM generation */
  tokenCallback?: ConversationTokenCallback;
  /** Enable stuck detection (default: true) */
  stuckDetection?: boolean;
  /** Custom thresholds for stuck detection */
  stuckDetectionThresholds?: Partial<StuckDetectionThresholds>;
  /** Security analyzer for evaluating action risks */
  securityAnalyzer?: SecurityAnalyzer;
  /** Initial secrets to provide to the conversation */
  secrets?: Record<string, SecretValue>;
}

/**
 * Implementation of events list for local conversations.
 * Stores typed events and provides indexing and search capabilities.
 */
class LocalEventsList implements IEventsList {
  private events: BaseEvent[] = [];
  private idToIndex: Map<string, number> = new Map();

  async addEvent(event: BaseEvent): Promise<void> {
    const index = this.events.length;
    this.events.push(event);
    this.idToIndex.set(event.id, index);
  }

  async getEvents(): Promise<BaseEvent[]> {
    return [...this.events];
  }

  /**
   * Get all events as an array (synchronous access).
   */
  getEventsSync(): BaseEvent[] {
    return [...this.events];
  }

  /**
   * Get an event by index.
   */
  getByIndex(index: number): BaseEvent | undefined {
    if (index < 0) {
      index = this.events.length + index;
    }
    return this.events[index];
  }

  /**
   * Get an event by ID.
   */
  getById(id: string): BaseEvent | undefined {
    const index = this.idToIndex.get(id);
    return index !== undefined ? this.events[index] : undefined;
  }

  /**
   * Get the index of an event by ID.
   */
  getIndex(id: string): number | undefined {
    return this.idToIndex.get(id);
  }

  /**
   * Get the number of events.
   */
  get length(): number {
    return this.events.length;
  }

  /**
   * Iterate over events.
   */
  [Symbol.iterator](): Iterator<BaseEvent> {
    return this.events[Symbol.iterator]();
  }

  getEventCounts(): {
    total: number;
    messages: number;
    actions: number;
    observations: number;
    errors: number;
  } {
    let messages = 0;
    let actions = 0;
    let observations = 0;
    let errors = 0;
    for (const event of this.events) {
      if (event.kind === 'MessageEvent') messages++;
      else if (event.kind === 'ActionEvent') actions++;
      else if (event.kind === 'ObservationEvent') observations++;
      else if (event.kind === 'AgentErrorEvent') errors++;
    }
    return { total: this.events.length, messages, actions, observations, errors };
  }
}

/**
 * Implementation of conversation state for local conversations.
 */
class LocalConversationState implements IConversationState {
  readonly id: ConversationID;
  readonly events: LocalEventsList;
  executionStatus: 'idle' | 'running' | 'paused' | 'finished' | 'waiting_for_confirmation' = 'idle';
  confirmationPolicy: ConfirmationPolicy = new NeverConfirm();
  securityAnalyzer?: SecurityAnalyzer;
  /** Actions waiting for confirmation: action_id -> ActionEvent */
  pendingActions: Map<string, ActionEvent> = new Map();

  constructor(id: ConversationID) {
    this.id = id;
    this.events = new LocalEventsList();
  }
}

/**
 * Built-in tools that require a functional workspace (execute_command, read_file, write_file).
 * These are only registered when the workspace supports them (i.e., not a stub LocalWorkspace).
 */
const WORKSPACE_TOOLS: Tool[] = [
  {
    type: 'function',
    function: {
      name: 'execute_command',
      description: TOOL_DESCRIPTIONS.execute_command,
      parameters: {
        type: 'object',
        properties: {
          command: {
            type: 'string',
            description:
              'The bash command to execute. You can only execute one bash command at a time. If you need to run multiple commands sequentially, use `&&` or `;` to chain them together.',
          },
          cwd: {
            type: 'string',
            description: 'Working directory for the command (optional, defaults to workspace root)',
          },
          timeout: {
            type: 'number',
            description: 'Optional timeout in seconds for the command (default: 30)',
          },
        },
        required: ['command'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'read_file',
      description: TOOL_DESCRIPTIONS.read_file,
      parameters: {
        type: 'object',
        properties: {
          path: {
            type: 'string',
            description: 'Path to the file to read (relative to workspace or absolute)',
          },
        },
        required: ['path'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'write_file',
      description: TOOL_DESCRIPTIONS.write_file,
      parameters: {
        type: 'object',
        properties: {
          path: {
            type: 'string',
            description: 'Path to the file to write (relative to workspace or absolute)',
          },
          content: {
            type: 'string',
            description: 'Content to write to the file',
          },
        },
        required: ['path', 'content'],
      },
    },
  },
];

/**
 * Built-in tools that work without a functional workspace.
 * These are always available when built-in tools are enabled.
 */
const STANDALONE_TOOLS: Tool[] = [
  {
    type: 'function',
    function: {
      name: 'think',
      description: TOOL_DESCRIPTIONS.think,
      parameters: {
        type: 'object',
        properties: {
          thought: {
            type: 'string',
            description: 'The thought to log',
          },
        },
        required: ['thought'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'finish',
      description: TOOL_DESCRIPTIONS.finish,
      parameters: {
        type: 'object',
        properties: {
          message: {
            type: 'string',
            description: 'Final message or summary to present to the user',
          },
        },
        required: ['message'],
      },
    },
  },
];

/**
 * Local conversation implementation that runs the agent loop locally.
 *
 * LocalConversation provides direct agent execution on the local system without
 * requiring a remote server. It integrates with an LLM (via ILLM interface) to
 * process messages and execute tool calls through the LocalWorkspace.
 *
 * Example:
 * ```typescript
 * const workspace = new LocalWorkspace({ workingDir: '/path/to/project' });
 * const llm = new OpenRouterLLM({ apiKey: 'your-key', defaultModel: 'anthropic/claude-3.5-sonnet' });
 * const conversation = new LocalConversation(agent, workspace, {
 *   llm,
 *   maxIterations: 500,
 *   systemPrompt: 'You are a helpful assistant...'
 * });
 * await conversation.start({ initialMessage: 'Hello!' });
 * await conversation.run();
 * await conversation.close();
 * ```
 */
export class LocalConversation implements IConversation {
  public readonly agent: AgentBase;
  public readonly workspace: IWorkspace;
  public readonly llm: ILLM;

  private _conversationId?: string;
  private _state?: LocalConversationState;
  private _title?: string;
  private callback?: ConversationCallbackType;
  private tokenCallback?: ConversationTokenCallback;
  private persistenceDir?: string;
  private systemPrompt: string;
  private maxIterations: number = 500;
  private messages: ChatMessage[] = [];
  private _isPaused: boolean = false;
  private _isFinished: boolean = false;
  private _isWaitingForConfirmation: boolean = false;
  private customTools?: Tool[];
  private toolExecutor?: ToolExecutor;
  private includeBuiltinTools: boolean;

  // New feature instances
  private secretRegistry: SecretRegistry;
  private stuckDetector?: StuckDetector;
  private stuckDetectionEnabled: boolean;
  private securityAnalyzer?: SecurityAnalyzer;

  constructor(agent: AgentBase, workspace: IWorkspace, options: LocalConversationOptions) {
    this.agent = agent;
    this.workspace = workspace;
    this.llm = options.llm;
    this.callback = options.callback;
    this.tokenCallback = options.tokenCallback;
    this._conversationId = options.conversationId;
    this.persistenceDir = options.persistenceDir;
    this.customTools = options.tools;
    this.toolExecutor = options.toolExecutor;
    // Include built-in tools by default only if no custom tools are provided
    this.includeBuiltinTools = options.includeBuiltinTools ?? !options.tools;

    // Initialize secret registry
    this.secretRegistry = new SecretRegistry();
    if (options.secrets) {
      this.secretRegistry.updateSecrets(options.secrets);
    }

    // Initialize stuck detection
    this.stuckDetectionEnabled = options.stuckDetection ?? true;
    if (this.stuckDetectionEnabled) {
      this.stuckDetector = new StuckDetector(options.stuckDetectionThresholds);
    }

    // Initialize security analyzer
    this.securityAnalyzer = options.securityAnalyzer;

    // Generate system prompt - use custom if provided, otherwise generate default
    this.systemPrompt =
      options.systemPrompt ||
      generateSystemPrompt({
        workingDir: workspace.workingDir,
      });

    if (options.maxIterations !== undefined) {
      this.maxIterations = options.maxIterations;
    }
  }

  /**
   * Check whether the workspace supports actual operations.
   * LocalWorkspace is a stub that throws on all operations.
   */
  private workspaceIsStub(): boolean {
    return this.workspace instanceof LocalWorkspace;
  }

  /**
   * Get the tools available to the agent.
   * Workspace-dependent tools (execute_command, read_file, write_file) are only
   * included when the workspace supports actual operations.
   */
  private getTools(): Tool[] {
    const tools: Tool[] = [];
    if (this.includeBuiltinTools) {
      tools.push(...STANDALONE_TOOLS);
      if (!this.workspaceIsStub()) {
        tools.push(...WORKSPACE_TOOLS);
      }
    }
    if (this.customTools) {
      tools.push(...this.customTools);
    }
    return tools;
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

    // Set max iterations if provided
    if (options.maxIterations !== undefined) {
      this.maxIterations = options.maxIterations;
    }

    // Initialize message history with system prompt
    this.messages = [{ role: 'system', content: this.systemPrompt }];

    // Add initial message if provided
    if (options.initialMessage) {
      await this.sendMessage(options.initialMessage);
    }

    this.emitEvent({
      type: 'message',
      timestamp: Date.now(),
      data: { kind: 'conversation_started', conversationId: this._conversationId },
    });
  }

  /**
   * Get conversation statistics.
   */
  async conversationStats(): Promise<ConversationStats> {
    if (!this._state) {
      return { total_events: 0, message_events: 0, action_events: 0, observation_events: 0 };
    }
    const counts = this._state.events.getEventCounts();
    return {
      total_events: counts.total,
      message_events: counts.messages,
      action_events: counts.actions,
      observation_events: counts.observations,
    };
  }

  /**
   * Send a message to the agent.
   */
  async sendMessage(message: string | Message): Promise<void> {
    const content = typeof message === 'string' ? message : JSON.stringify(message);

    // Add user message to history
    this.messages.push({ role: 'user', content });

    // Record the event
    this.emitEvent({
      type: 'message',
      timestamp: Date.now(),
      data: { kind: 'user_message', content },
    });
  }

  /**
   * Execute the agent loop to process messages.
   *
   * This runs the agent until:
   * - The agent calls the finish() tool
   * - Maximum iterations reached
   * - pause() is called
   * - An error occurs
   */
  async run(): Promise<void> {
    if (!this._state) {
      throw new Error('Conversation not started. Call start() first.');
    }

    this._state.executionStatus = 'running';
    this._isPaused = false;
    this._isFinished = false;

    let iterations = 0;

    const tools = this.getTools();

    while (iterations < this.maxIterations && !this._isPaused && !this._isFinished) {
      iterations++;

      try {
        // Get LLM response
        const response = await this.llm.chatCompletion({
          messages: this.messages,
          tools: tools.length > 0 ? tools : undefined,
          toolChoice: tools.length > 0 ? 'auto' : undefined,
        });

        const choice = response.choices[0];
        if (!choice) {
          throw new Error('No response from LLM');
        }

        const assistantMessage = choice.message;

        // Add assistant message to history
        this.messages.push({
          role: 'assistant',
          content: assistantMessage.content || '',
          tool_calls: assistantMessage.tool_calls,
        });

        // Emit the assistant's response
        if (assistantMessage.content) {
          this.emitEvent({
            type: 'message',
            timestamp: Date.now(),
            data: { kind: 'assistant_message', content: assistantMessage.content },
          });
        }

        // Handle tool calls
        if (assistantMessage.tool_calls && assistantMessage.tool_calls.length > 0) {
          for (const toolCall of assistantMessage.tool_calls) {
            if (this._isPaused || this._isFinished) break;
            await this.handleToolCall(toolCall);
          }
        } else if (choice.finish_reason === 'stop') {
          // No tool calls and stop reason - agent is done
          this._isFinished = true;
        }
      } catch (error) {
        this.emitEvent({
          type: 'error',
          timestamp: Date.now(),
          data: {
            kind: 'agent_error',
            error: error instanceof Error ? error.message : String(error),
          },
        });
        this._state.executionStatus = 'finished';
        throw error;
      }
    }

    this._state.executionStatus = this._isPaused ? 'paused' : 'finished';

    if (iterations >= this.maxIterations && !this._isFinished) {
      this.emitEvent({
        type: 'observation',
        timestamp: Date.now(),
        data: { kind: 'max_iterations_reached', iterations },
      });
    }
  }

  /**
   * Handle a tool call from the LLM.
   */
  private async handleToolCall(toolCall: ToolCall): Promise<void> {
    const { name, arguments: argsString } = toolCall.function;

    this.emitEvent({
      type: 'action',
      timestamp: Date.now(),
      data: { kind: 'tool_call', tool: name, arguments: argsString },
    });

    let result: string;

    try {
      // If a custom tool executor is provided, use it for all tool calls
      if (this.toolExecutor) {
        result = await this.toolExecutor(toolCall);
        // Check if this was a finish tool call
        if (name === 'finish') {
          this._isFinished = true;
        }
      } else {
        // Use built-in tool handling
        result = await this.executeBuiltinTool(toolCall);
      }
    } catch (error) {
      result = `Error executing ${name}: ${error instanceof Error ? error.message : String(error)}`;
    }

    // Add tool result to messages
    this.messages.push({
      role: 'tool',
      content: result,
      tool_call_id: toolCall.id,
    });

    this.emitEvent({
      type: 'observation',
      timestamp: Date.now(),
      data: { kind: 'tool_result', tool: name, result },
    });
  }

  /**
   * Execute a built-in tool.
   */
  private async executeBuiltinTool(toolCall: ToolCall): Promise<string> {
    const { name, arguments: argsString } = toolCall.function;
    const args = JSON.parse(argsString);

    switch (name) {
      case 'execute_command': {
        const cmdResult = await this.workspace.executeCommand(args.command, args.cwd);
        let result = `Exit code: ${cmdResult.exit_code}\n`;
        if (cmdResult.stdout) result += `stdout:\n${cmdResult.stdout}\n`;
        if (cmdResult.stderr) result += `stderr:\n${cmdResult.stderr}`;
        if (cmdResult.timeout_occurred) result += '\n(Command timed out)';
        return result;
      }

      case 'read_file': {
        const content = await this.workspace.downloadAsText(args.path);
        return content;
      }

      case 'write_file': {
        const uploadResult = await this.workspace.fileUpload(args.content, args.path);
        if (uploadResult.success) {
          return `Successfully wrote ${uploadResult.file_size} bytes to ${args.path}`;
        } else {
          return `Failed to write file: ${uploadResult.error}`;
        }
      }

      case 'think': {
        this.emitEvent({
          type: 'observation',
          timestamp: Date.now(),
          data: { kind: 'think', thought: args.thought },
        });
        return 'Your thought has been logged.';
      }

      case 'finish': {
        this._isFinished = true;
        this.emitEvent({
          type: 'message',
          timestamp: Date.now(),
          data: { kind: 'finish', message: args.message },
        });
        return 'Task completed.';
      }

      default:
        return `Unknown tool: ${name}`;
    }
  }

  /**
   * Pause agent execution.
   */
  async pause(): Promise<void> {
    this._isPaused = true;
    if (this._state) {
      this._state.executionStatus = 'paused';
    }
    this.emitEvent({
      type: 'message',
      timestamp: Date.now(),
      data: { kind: 'paused' },
    });
  }

  /**
   * Set the confirmation policy.
   */
  async setConfirmationPolicy(policy: ConfirmationPolicyBase | ConfirmationPolicy): Promise<void> {
    if (this._state) {
      // If it's a ConfirmationPolicyBase (old interface), wrap it
      if ('requiresConfirmation' in policy) {
        this._state.confirmationPolicy = policy as ConfirmationPolicy;
      } else {
        // Create a simple wrapper
        this._state.confirmationPolicy = {
          type: (policy as ConfirmationPolicyBase).type,
          requiresConfirmation: () => (policy as ConfirmationPolicyBase).type === 'always',
        };
      }
    }
  }

  /**
   * Send a confirmation response.
   *
   * Note: Confirmation handling is not yet fully implemented in LocalConversation.
   */
  async sendConfirmationResponse(accept: boolean, reason?: string): Promise<void> {
    this.emitEvent({
      type: 'message',
      timestamp: Date.now(),
      data: { kind: 'confirmation_response', accept, reason },
    });
    // Resume execution if paused for confirmation
    if (accept) {
      this._isPaused = false;
    }
  }

  async setTitle(title: string): Promise<void> {
    this._title = title;
  }

  /**
   * Generate a title for the conversation using the LLM.
   */
  async generateTitle(maxLength: number = 50, _llm?: LLM): Promise<string> {
    // LocalConversation always uses its own ILLM instance for title generation.
    // The llm parameter exists for interface compatibility with RemoteConversation.
    const llmToUse = this.llm;

    // Get a summary of the conversation
    const userMessages = this.messages
      .filter((m) => m.role === 'user')
      .map((m) => (typeof m.content === 'string' ? m.content : JSON.stringify(m.content)))
      .slice(0, 3)
      .join('\n');

    if (!userMessages) {
      return 'New Conversation';
    }

    const prompt = `Generate a short title (max ${maxLength} characters) for a conversation that starts with:\n\n${userMessages}\n\nRespond with only the title, no quotes or explanation.`;

    const title = await llmToUse.generate(prompt);
    return title.slice(0, maxLength).trim();
  }

  /**
   * Update secrets available to the agent.
   * Secrets are stored in the SecretRegistry and can be used for:
   * - Environment variable injection into commands
   * - Output masking to prevent accidental exposure
   */
  async updateSecrets(secrets: Record<string, SecretValue>): Promise<void> {
    this.secretRegistry.updateSecrets(secrets);
  }

  /**
   * Ask the agent a simple, stateless question and get a direct LLM response.
   *
   * This bypasses the normal conversation flow and does NOT modify, persist,
   * or become part of the conversation state. The request is not remembered by
   * the main agent, no events are recorded, and execution status is untouched.
   *
   * @param question - A simple string question to ask the agent
   * @returns A string response from the agent
   */
  async askAgent(question: string): Promise<string> {
    // Build context from recent conversation history
    const contextMessages = this.messages.slice(-10); // Last 10 messages for context

    const askPrompt = `Based on the conversation context, please answer this question concisely:

Question: ${question}

Provide a direct answer without using any tools.`;

    const messages: ChatMessage[] = [...contextMessages, { role: 'user', content: askPrompt }];

    // Make a simple completion without tools
    const response = await this.llm.chatCompletion({
      messages,
      // No tools - just get a direct answer
    });

    return response.choices[0]?.message?.content || 'Unable to generate a response.';
  }

  /**
   * Reject all pending actions awaiting confirmation.
   *
   * @param reason - The reason for rejection
   */
  async rejectPendingActions(reason: string = 'User rejected the action'): Promise<void> {
    if (!this._state) return;

    for (const [actionId, action] of this._state.pendingActions) {
      // Emit rejection event
      const rejectEvent: UserRejectObservation = {
        id: generateEventId(),
        kind: 'UserRejectObservation',
        timestamp: new Date().toISOString(),
        source: 'user',
        action_id: actionId,
        tool_name: action.tool_name ?? '',
        tool_call_id: action.tool_call_id ?? '',
        rejection_reason: reason,
        rejection_source: 'user',
      };

      this.emitTypedEvent(rejectEvent);

      // Add rejection as tool result
      this.messages.push({
        role: 'tool',
        content: `Action rejected: ${reason}`,
        tool_call_id: action.tool_call_id,
      });
    }

    // Clear pending actions
    this._state.pendingActions.clear();

    // Resume if was waiting for confirmation
    if (this._isWaitingForConfirmation) {
      this._isWaitingForConfirmation = false;
      this._state.executionStatus = 'running';
    }
  }

  /**
   * Set the security analyzer for evaluating action risks.
   *
   * @param analyzer - The security analyzer to use, or null to disable
   */
  setSecurityAnalyzer(analyzer: SecurityAnalyzer | null): void {
    this.securityAnalyzer = analyzer || undefined;
    if (this._state) {
      this._state.securityAnalyzer = this.securityAnalyzer;
    }
  }

  /**
   * Check if the agent is currently stuck using the stuck detector.
   *
   * @returns StuckDetectionResult with details about any detected stuck pattern
   */
  checkIfStuck(): StuckDetectionResult {
    if (!this.stuckDetector || !this._state) {
      return { isStuck: false };
    }

    const events = this._state.events.getEventsSync();
    return this.stuckDetector.isStuck(events);
  }

  /**
   * Get the secret registry for direct access.
   * Useful for masking secrets in custom output handling.
   */
  getSecretRegistry(): SecretRegistry {
    return this.secretRegistry;
  }

  /**
   * Mask any secrets in the given text.
   *
   * @param text - Text that may contain secret values
   * @returns Text with secret values replaced by <secret-hidden>
   */
  maskSecrets(text: string): string {
    return this.secretRegistry.maskSecretsInOutput(text);
  }

  /**
   * Start WebSocket client.
   *
   * NOTE: LocalConversation doesn't use WebSocket since it runs locally.
   */
  async startWebSocketClient(): Promise<void> {
    // No-op for local conversation
  }

  /**
   * Stop WebSocket client.
   *
   * NOTE: LocalConversation doesn't use WebSocket.
   */
  async stopWebSocketClient(): Promise<void> {
    // No-op for local conversation
  }

  /**
   * Close the conversation and cleanup resources.
   */
  async close(): Promise<void> {
    this._isPaused = true;
    this._isFinished = true;
    if (this._state) {
      this._state.executionStatus = 'finished';
    }
    this.workspace.close();
    this.llm.close();
    const stateEvent: ConversationStateUpdateEvent = {
      id: generateEventId(),
      kind: 'ConversationStateUpdateEvent',
      timestamp: new Date().toISOString(),
      source: 'system',
      key: 'status',
      value: 'closed',
    };
    this.emitTypedEvent(stateEvent);
  }

  /**
   * Get the current message history.
   */
  getMessages(): ChatMessage[] {
    return [...this.messages];
  }

  /**
   * Emit a typed event and call the callback if provided.
   */
  private emitTypedEvent(event: BaseEvent): void {
    if (this._state) {
      this._state.events.addEvent(event);
    }
    if (this.callback) {
      // Convert to the callback's expected Event format
      this.callback(event as Event);
    }
  }

  /**
   * Emit an event (legacy format) and call the callback if provided.
   * @deprecated Use emitTypedEvent instead
   */
  private emitEvent(event: { type: string; timestamp: number; data: unknown }): void {
    // Convert to typed event
    const typedEvent: BaseEvent = {
      id: generateEventId(),
      kind: (event.data as { kind?: string })?.kind || event.type,
      timestamp: new Date(event.timestamp).toISOString(),
      ...(event.data as Record<string, unknown>),
    };

    if (this._state) {
      this._state.events.addEvent(typedEvent);
    }
    if (this.callback) {
      this.callback(typedEvent as Event);
    }
  }

  /**
   * Generate a unique conversation ID.
   */
  private generateConversationId(): string {
    return crypto.randomUUID();
  }
}
