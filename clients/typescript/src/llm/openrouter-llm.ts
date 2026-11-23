/**
 * OpenRouter LLM implementation
 *
 * This implements the ILLM interface using the OpenRouter SDK,
 * providing access to 300+ models through a unified API.
 */

import { OpenRouter } from '@openrouter/sdk';
import type {
  ILLM,
  BaseLLMOptions,
  ChatCompletionOptions,
  ChatCompletionResponse,
  ChatCompletionChunk,
  ChatMessage,
  Tool,
  ToolCall,
  TokenCallbackType,
} from './base';

// Define response types locally since the SDK's module resolution is complex
interface OpenRouterChatResponse {
  id: string;
  choices: Array<{
    message?: {
      content?: string | null;
      toolCalls?: ToolCall[];
    };
    finishReason?: string;
  }>;
  created: number;
  model: string;
  usage?: {
    promptTokens?: number;
    completionTokens?: number;
    totalTokens?: number;
  };
}

interface OpenRouterStreamChunk {
  id?: string;
  model?: string;
  choices: Array<{
    index?: number;
    delta?: {
      role?: string;
      content?: string;
      toolCalls?: Array<{
        index: number;
        id?: string;
        type?: string;
        function?: {
          name?: string;
          arguments?: string;
        };
      }>;
    };
    finishReason?: string;
  }>;
  created?: number;
}

/**
 * Options for creating an OpenRouterLLM instance
 */
export interface OpenRouterLLMOptions extends BaseLLMOptions {
  /** Site URL for rankings on openrouter.ai (optional) */
  siteUrl?: string;
  /** Site title for rankings on openrouter.ai (optional) */
  siteTitle?: string;
}

// OpenRouter SDK message types (simplified for our use)
type OpenRouterMessage =
  | { role: 'system'; content: string; name?: string }
  | { role: 'user'; content: string; name?: string }
  | { role: 'assistant'; content: string | null; name?: string; toolCalls?: ToolCall[] }
  | { role: 'tool'; content: string; toolCallId: string };

/**
 * OpenRouter LLM implementation using the official @openrouter/sdk.
 *
 * Provides access to 300+ AI models through OpenRouter's unified API.
 *
 * Example:
 * ```typescript
 * const llm = new OpenRouterLLM({
 *   apiKey: 'your-api-key',
 *   defaultModel: 'anthropic/claude-3.5-sonnet'
 * });
 *
 * const response = await llm.generate('Hello, how are you?');
 * console.log(response);
 *
 * // Or with full control:
 * const completion = await llm.chatCompletion({
 *   messages: [{ role: 'user', content: 'Explain quantum computing' }],
 *   temperature: 0.7,
 *   maxTokens: 1000
 * });
 * ```
 */
export class OpenRouterLLM implements ILLM {
  private client: OpenRouter;
  public readonly defaultModel: string;
  private readonly defaultTemperature?: number;
  private readonly defaultMaxTokens?: number;

  constructor(options: OpenRouterLLMOptions) {
    this.client = new OpenRouter({
      apiKey: options.apiKey,
      httpReferer: options.siteUrl,
      appTitle: options.siteTitle,
    });

    this.defaultModel = options.defaultModel || 'anthropic/claude-3.5-sonnet';
    this.defaultTemperature = options.defaultTemperature;
    this.defaultMaxTokens = options.defaultMaxTokens;
  }

  /**
   * Convert our ChatMessage format to OpenRouter's format
   */
  private convertMessages(messages: ChatMessage[]): OpenRouterMessage[] {
    return messages.map((msg): OpenRouterMessage => {
      if (msg.role === 'system') {
        return {
          role: 'system',
          content: typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content),
          name: msg.name,
        };
      } else if (msg.role === 'user') {
        return {
          role: 'user',
          content: typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content),
          name: msg.name,
        };
      } else if (msg.role === 'assistant') {
        return {
          role: 'assistant',
          content: typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content),
          name: msg.name,
          toolCalls: msg.tool_calls,
        };
      } else {
        // tool role
        return {
          role: 'tool',
          content: typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content),
          toolCallId: msg.tool_call_id || '',
        };
      }
    });
  }

  /**
   * Convert our Tool format to OpenRouter's format
   */
  private convertTools(tools: Tool[]) {
    return tools.map((tool) => ({
      type: 'function' as const,
      function: {
        name: tool.function.name,
        description: tool.function.description,
        parameters: tool.function.parameters,
      },
    }));
  }

  /**
   * Build the common request parameters shared by chatCompletion and chatCompletionStream.
   */
  private buildRequestParams(
    options: ChatCompletionOptions | Omit<ChatCompletionOptions, 'stream'>,
    stream: boolean
  ): Record<string, unknown> {
    const requestParams: Record<string, unknown> = {
      model: options.model || this.defaultModel,
      messages: this.convertMessages(options.messages),
      stream,
    };

    if (options.temperature !== undefined) {
      requestParams.temperature = options.temperature;
    } else if (this.defaultTemperature !== undefined) {
      requestParams.temperature = this.defaultTemperature;
    }

    if (options.maxTokens !== undefined) {
      requestParams.maxTokens = options.maxTokens;
    } else if (this.defaultMaxTokens !== undefined) {
      requestParams.maxTokens = this.defaultMaxTokens;
    }

    if (options.tools && options.tools.length > 0) {
      requestParams.tools = this.convertTools(options.tools);
    }

    if (options.toolChoice) {
      requestParams.toolChoice = options.toolChoice;
    }

    if (options.stop) {
      requestParams.stop = options.stop;
    }

    return requestParams;
  }

  async chatCompletion(options: ChatCompletionOptions): Promise<ChatCompletionResponse> {
    const model = options.model || this.defaultModel;
    const requestParams = this.buildRequestParams(options, false);

    // Cast to expected type - SDK handles validation
    const response = (await this.client.chat.send(
      requestParams as Parameters<typeof this.client.chat.send>[0]
    )) as OpenRouterChatResponse;

    // Convert to our response format
    return {
      id: response.id || '',
      model: response.model || model,
      choices: response.choices.map(
        (choice: OpenRouterChatResponse['choices'][0], index: number) => ({
          index,
          message: {
            role: 'assistant' as const,
            content: choice.message?.content || null,
            tool_calls: choice.message?.toolCalls as ToolCall[] | undefined,
          },
          finish_reason:
            (choice.finishReason as 'stop' | 'length' | 'tool_calls' | 'content_filter') || null,
        })
      ),
      usage: response.usage
        ? {
            prompt_tokens: response.usage.promptTokens || 0,
            completion_tokens: response.usage.completionTokens || 0,
            total_tokens: response.usage.totalTokens || 0,
          }
        : undefined,
      created: response.created || Date.now(),
    };
  }

  async *chatCompletionStream(
    options: Omit<ChatCompletionOptions, 'stream'>
  ): AsyncIterable<ChatCompletionChunk> {
    const model = options.model || this.defaultModel;
    const requestParams = this.buildRequestParams(options, true);

    const stream = await this.client.chat.send(
      requestParams as Parameters<typeof this.client.chat.send>[0]
    );

    // The stream is an async iterable when stream: true
    for await (const chunk of stream as AsyncIterable<OpenRouterStreamChunk>) {
      yield {
        id: chunk.id || '',
        model: chunk.model || model,
        choices: chunk.choices.map((choice: OpenRouterStreamChunk['choices'][0]) => ({
          index: choice.index || 0,
          delta: {
            role: choice.delta?.role as 'assistant' | undefined,
            content: choice.delta?.content,
            tool_calls: choice.delta?.toolCalls?.map(
              (
                tc: NonNullable<
                  NonNullable<OpenRouterStreamChunk['choices'][0]['delta']>['toolCalls']
                >[0]
              ) => ({
                index: tc.index,
                id: tc.id,
                type: tc.type as 'function' | undefined,
                function: tc.function,
              })
            ),
          },
          finish_reason:
            (choice.finishReason as 'stop' | 'length' | 'tool_calls' | 'content_filter') || null,
        })),
        created: chunk.created || Date.now(),
      };
    }
  }

  async generate(prompt: string, systemPrompt?: string): Promise<string> {
    const messages: ChatMessage[] = [];

    if (systemPrompt) {
      messages.push({ role: 'system', content: systemPrompt });
    }
    messages.push({ role: 'user', content: prompt });

    const response = await this.chatCompletion({ messages });

    return response.choices[0]?.message?.content || '';
  }

  /**
   * Chat completion with streaming and token callback.
   * Invokes the callback for each token received during streaming.
   */
  async chatCompletionWithCallback(
    options: Omit<ChatCompletionOptions, 'stream'>,
    onToken: TokenCallbackType
  ): Promise<ChatCompletionResponse> {
    const model = options.model || this.defaultModel;
    let accumulated = '';
    let tokenIndex = 0;
    let lastChunk: ChatCompletionChunk | null = null;
    const toolCalls: Map<
      number,
      { id: string; type: 'function'; function: { name: string; arguments: string } }
    > = new Map();

    for await (const chunk of this.chatCompletionStream(options)) {
      lastChunk = chunk;

      for (const choice of chunk.choices) {
        // Handle content tokens
        if (choice.delta?.content) {
          accumulated += choice.delta.content;
          onToken({
            token: choice.delta.content,
            isFinal: false,
            accumulated,
            index: tokenIndex++,
            model: chunk.model,
          });
        }

        // Accumulate tool calls
        if (choice.delta?.tool_calls) {
          for (const tc of choice.delta.tool_calls) {
            const existing = toolCalls.get(tc.index);
            if (existing) {
              // Append to existing tool call
              if (tc.function?.arguments) {
                existing.function.arguments += tc.function.arguments;
              }
            } else if (tc.id) {
              // New tool call
              toolCalls.set(tc.index, {
                id: tc.id,
                type: 'function',
                function: {
                  name: tc.function?.name || '',
                  arguments: tc.function?.arguments || '',
                },
              });
            }
          }
        }
      }
    }

    // Send final token event
    onToken({
      token: '',
      isFinal: true,
      accumulated,
      index: tokenIndex,
      model,
    });

    // Construct final response
    const finalToolCalls = toolCalls.size > 0 ? Array.from(toolCalls.values()) : undefined;

    return {
      id: lastChunk?.id || '',
      model: lastChunk?.model || model,
      choices: [
        {
          index: 0,
          message: {
            role: 'assistant',
            content: accumulated || null,
            tool_calls: finalToolCalls,
          },
          finish_reason: lastChunk?.choices[0]?.finish_reason || 'stop',
        },
      ],
      created: lastChunk?.created || Date.now(),
    };
  }

  /**
   * Generate with streaming and token callback.
   */
  async generateWithCallback(
    prompt: string,
    systemPrompt?: string,
    onToken?: TokenCallbackType
  ): Promise<string> {
    const messages: ChatMessage[] = [];

    if (systemPrompt) {
      messages.push({ role: 'system', content: systemPrompt });
    }
    messages.push({ role: 'user', content: prompt });

    if (onToken) {
      const response = await this.chatCompletionWithCallback({ messages }, onToken);
      return response.choices[0]?.message?.content || '';
    } else {
      return this.generate(prompt, systemPrompt);
    }
  }

  close(): void {
    // OpenRouter SDK doesn't require explicit cleanup
  }
}
