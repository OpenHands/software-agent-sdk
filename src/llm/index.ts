/**
 * LLM module exports
 */

// Base types and interface
export type {
  ILLM,
  BaseLLMOptions,
  LLMProviderType,
  MessageRole,
  TextContent,
  ImageContent,
  ContentPart,
  ChatMessage,
  Tool,
  ToolCall,
  ChatCompletionOptions,
  ChatCompletionChoice,
  TokenUsage,
  ChatCompletionResponse,
  ChatCompletionChunk,
  TokenCallbackType,
  TokenStreamEvent,
} from './base';

// OpenRouter implementation
export { OpenRouterLLM } from './openrouter-llm';
export type { OpenRouterLLMOptions } from './openrouter-llm';

// Factory functions and LLM class
export { LLM, createLLM, createOpenRouterLLM } from './llm';
export type { LLMOptions, CreateLLMOptions } from './llm';
