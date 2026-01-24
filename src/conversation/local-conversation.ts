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
import {
  IConversation,
  IConversationState,
  IEventsList,
  BaseConversationOptions,
} from './base';
import {
  ILLM,
  ChatMessage,
  Tool,
  ToolCall,
} from '../llm/base';
import { generateSystemPrompt, TOOL_DESCRIPTIONS } from '../prompts';

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
}

/**
 * Event types for local conversation
 */
interface ConversationEvent {
  type: 'message' | 'action' | 'observation' | 'error';
  timestamp: number;
  data: unknown;
}

/**
 * Implementation of events list for local conversations.
 */
class LocalEventsList implements IEventsList {
  private events: ConversationEvent[] = [];

  async addEvent(event: ConversationEvent): Promise<void> {
    this.events.push(event);
  }

  async getEvents(): Promise<ConversationEvent[]> {
    return [...this.events];
  }

  getEventCounts(): { total: number; messages: number; actions: number; observations: number } {
    let messages = 0;
    let actions = 0;
    let observations = 0;
    for (const event of this.events) {
      if (event.type === 'message') messages++;
      else if (event.type === 'action') actions++;
      else if (event.type === 'observation') observations++;
    }
    return { total: this.events.length, messages, actions, observations };
  }
}

/**
 * Implementation of conversation state for local conversations.
 */
class LocalConversationState implements IConversationState {
  readonly id: ConversationID;
  readonly events: LocalEventsList;
  executionStatus: 'idle' | 'running' | 'paused' | 'finished' = 'idle';
  confirmationPolicy?: ConfirmationPolicyBase;

  constructor(id: ConversationID) {
    this.id = id;
    this.events = new LocalEventsList();
  }
}

/**
 * Built-in tools available to the agent
 * Aligned with the Python SDK's tool definitions
 */
const BUILTIN_TOOLS: Tool[] = [
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
            description: 'The bash command to execute. You can only execute one bash command at a time. If you need to run multiple commands sequentially, use `&&` or `;` to chain them together.',
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
 *   maxIterations: 50,
 *   systemPrompt: 'You are a helpful assistant...'
 * });
 * await conversation.start({ initialMessage: 'Hello!' });
 * await conversation.run();
 * await conversation.close();
 * ```
 */
export class LocalConversation implements IConversation {
  public readonly agent: AgentBase;
  public readonly workspace: LocalWorkspace;
  public readonly llm: ILLM;

  private _conversationId?: string;
  private _state?: LocalConversationState;
  private callback?: ConversationCallbackType;
  private persistenceDir?: string;
  private systemPrompt: string;
  private maxIterations: number = 50;
  private messages: ChatMessage[] = [];
  private _isPaused: boolean = false;
  private _isFinished: boolean = false;
  private secrets: Record<string, SecretValue> = {};

  constructor(
    agent: AgentBase,
    workspace: LocalWorkspace,
    options: LocalConversationOptions
  ) {
    this.agent = agent;
    this.workspace = workspace;
    this.llm = options.llm;
    this.callback = options.callback;
    this._conversationId = options.conversationId;
    this.persistenceDir = options.persistenceDir;

    // Generate system prompt - use custom if provided, otherwise generate default
    this.systemPrompt = options.systemPrompt || generateSystemPrompt({
      workingDir: workspace.workingDir,
    });

    if (options.maxIterations !== undefined) {
      this.maxIterations = options.maxIterations;
    }
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
    this.messages = [
      { role: 'system', content: this.systemPrompt },
    ];

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

    while (iterations < this.maxIterations && !this._isPaused && !this._isFinished) {
      iterations++;

      try {
        // Get LLM response
        const response = await this.llm.chatCompletion({
          messages: this.messages,
          tools: BUILTIN_TOOLS,
          toolChoice: 'auto',
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
          data: { kind: 'agent_error', error: error instanceof Error ? error.message : String(error) },
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
      const args = JSON.parse(argsString);

      switch (name) {
        case 'execute_command': {
          const cmdResult = await this.workspace.executeCommand(args.command, args.cwd);
          result = `Exit code: ${cmdResult.exit_code}\n`;
          if (cmdResult.stdout) result += `stdout:\n${cmdResult.stdout}\n`;
          if (cmdResult.stderr) result += `stderr:\n${cmdResult.stderr}`;
          if (cmdResult.timeout_occurred) result += '\n(Command timed out)';
          break;
        }

        case 'read_file': {
          const content = await this.workspace.downloadAsText(args.path);
          result = content;
          break;
        }

        case 'write_file': {
          const uploadResult = await this.workspace.fileUpload(args.content, args.path);
          if (uploadResult.success) {
            result = `Successfully wrote ${uploadResult.file_size} bytes to ${args.path}`;
          } else {
            result = `Failed to write file: ${uploadResult.error}`;
          }
          break;
        }

        case 'think': {
          // Think tool just logs the thought - no execution needed
          result = 'Your thought has been logged.';
          this.emitEvent({
            type: 'observation',
            timestamp: Date.now(),
            data: { kind: 'think', thought: args.thought },
          });
          break;
        }

        case 'finish': {
          result = 'Task completed.';
          this._isFinished = true;
          this.emitEvent({
            type: 'message',
            timestamp: Date.now(),
            data: { kind: 'finish', message: args.message },
          });
          break;
        }

        default:
          result = `Unknown tool: ${name}`;
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
  async setConfirmationPolicy(policy: ConfirmationPolicyBase): Promise<void> {
    if (this._state) {
      this._state.confirmationPolicy = policy;
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

  /**
   * Generate a title for the conversation using the LLM.
   */
  async generateTitle(maxLength: number = 50, llm?: LLM): Promise<string> {
    const llmToUse = llm || this.llm;

    // Get a summary of the conversation
    const userMessages = this.messages
      .filter(m => m.role === 'user')
      .map(m => typeof m.content === 'string' ? m.content : JSON.stringify(m.content))
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
   */
  async updateSecrets(secrets: Record<string, SecretValue>): Promise<void> {
    this.secrets = { ...this.secrets, ...secrets };
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
    this.emitEvent({
      type: 'message',
      timestamp: Date.now(),
      data: { kind: 'conversation_closed' },
    });
  }

  /**
   * Get the current message history.
   */
  getMessages(): ChatMessage[] {
    return [...this.messages];
  }

  /**
   * Emit an event and call the callback if provided.
   */
  private emitEvent(event: ConversationEvent): void {
    if (this._state) {
      this._state.events.addEvent(event);
    }
    if (this.callback) {
      // Convert to Event format expected by callback
      const callbackEvent: Event = {
        id: this.generateEventId(),
        kind: (event.data as { kind?: string })?.kind || event.type,
        timestamp: new Date(event.timestamp).toISOString(),
        ...event.data as Record<string, unknown>,
      };
      this.callback(callbackEvent);
    }
  }

  /**
   * Generate a unique event ID.
   */
  private generateEventId(): string {
    return `evt_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
  }

  /**
   * Generate a unique conversation ID.
   */
  private generateConversationId(): string {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      const v = c === 'x' ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }
}
