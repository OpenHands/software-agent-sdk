/**
 * Base LLM interface and types
 *
 * This file defines the abstract interface that all LLM implementations must follow.
 * It provides a unified way to interact with different LLM providers (OpenRouter, OpenAI, etc.)
 */

/**
 * Message role types for chat conversations
 */
export type MessageRole = 'system' | 'user' | 'assistant' | 'tool';

/**
 * Content types for messages
 */
export interface TextContent {
  type: 'text';
  text: string;
}

export interface ImageContent {
  type: 'image_url';
  image_url: {
    url: string;
    detail?: 'auto' | 'low' | 'high';
  };
}

export type ContentPart = TextContent | ImageContent;

/**
 * A message in the chat conversation
 */
export interface ChatMessage {
  role: MessageRole;
  content: string | ContentPart[];
  name?: string;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
}

/**
 * Tool/function definition for the LLM
 */
export interface Tool {
  type: 'function';
  function: {
    name: string;
    description?: string;
    parameters?: Record<string, unknown>;
  };
}

/**
 * A tool call requested by the LLM
 */
export interface ToolCall {
  id: string;
  type: 'function';
  function: {
    name: string;
    arguments: string;
  };
}

/**
 * Options for a chat completion request
 */
export interface ChatCompletionOptions {
  /** The model to use (e.g., 'anthropic/claude-3.5-sonnet', 'openai/gpt-4o') */
  model?: string;
  /** The messages to send */
  messages: ChatMessage[];
  /** Temperature for sampling (0-2) */
  temperature?: number;
  /** Maximum tokens to generate */
  maxTokens?: number;
  /** Tools/functions available to the model */
  tools?: Tool[];
  /** How the model should use tools: 'auto', 'none', or specific tool */
  toolChoice?: 'auto' | 'none' | 'required' | { type: 'function'; function: { name: string } };
  /** Stop sequences */
  stop?: string[];
  /** Whether to stream the response */
  stream?: boolean;
}

/**
 * A choice in the completion response
 */
export interface ChatCompletionChoice {
  index: number;
  message: {
    role: 'assistant';
    content: string | null;
    tool_calls?: ToolCall[];
  };
  finish_reason: 'stop' | 'length' | 'tool_calls' | 'content_filter' | null;
}

/**
 * Token usage information
 */
export interface TokenUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

/**
 * Response from a chat completion request
 */
export interface ChatCompletionResponse {
  id: string;
  model: string;
  choices: ChatCompletionChoice[];
  usage?: TokenUsage;
  created: number;
}

/**
 * A streaming chunk from the LLM
 */
export interface ChatCompletionChunk {
  id: string;
  model: string;
  choices: Array<{
    index: number;
    delta: {
      role?: 'assistant';
      content?: string | null;
      tool_calls?: Array<{
        index: number;
        id?: string;
        type?: 'function';
        function?: {
          name?: string;
          arguments?: string;
        };
      }>;
    };
    finish_reason: 'stop' | 'length' | 'tool_calls' | 'content_filter' | null;
  }>;
  created: number;
}

/**
 * Base options for creating an LLM client
 */
export interface BaseLLMOptions {
  /** API key for authentication */
  apiKey: string;
  /** Default model to use if not specified in requests */
  defaultModel?: string;
  /** Base URL for the API (optional, for custom endpoints) */
  baseUrl?: string;
  /** Default temperature */
  defaultTemperature?: number;
  /** Default max tokens */
  defaultMaxTokens?: number;
}

/**
 * Abstract interface for LLM implementations.
 *
 * All LLM providers (OpenRouter, OpenAI, Anthropic, etc.) should implement this interface
 * to provide a unified way to interact with different models.
 */
export interface ILLM {
  /** The default model for this LLM instance */
  readonly defaultModel: string;

  /**
   * Send a chat completion request and get a full response.
   *
   * @param options - The completion options including messages and model settings
   * @returns The completion response with generated content
   */
  chatCompletion(options: ChatCompletionOptions): Promise<ChatCompletionResponse>;

  /**
   * Send a chat completion request and stream the response.
   *
   * @param options - The completion options (stream will be set to true)
   * @returns An async iterable of completion chunks
   */
  chatCompletionStream(
    options: Omit<ChatCompletionOptions, 'stream'>
  ): AsyncIterable<ChatCompletionChunk>;

  /**
   * Simple helper to send a single message and get a text response.
   *
   * @param prompt - The user message to send
   * @param systemPrompt - Optional system prompt
   * @returns The assistant's response text
   */
  generate(prompt: string, systemPrompt?: string): Promise<string>;

  /**
   * Close/cleanup any resources held by the LLM client.
   */
  close(): void;
}

/**
 * Type discriminator for LLM provider types.
 */
export type LLMProviderType = 'openrouter' | 'openai' | 'anthropic' | 'custom';
