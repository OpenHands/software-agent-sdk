/**
 * LLM factory and utility functions
 *
 * This module provides a convenient factory pattern for creating LLM instances.
 */

import type { ILLM, LLMProviderType } from './base';
import { OpenRouterLLM, OpenRouterLLMOptions } from './openrouter-llm';

/**
 * Union type for all LLM options
 */
export type LLMOptions = OpenRouterLLMOptions;

/**
 * Options for creating an LLM with explicit provider selection
 */
export interface CreateLLMOptions {
  provider: LLMProviderType;
  options: LLMOptions;
}

/**
 * Factory function to create an LLM instance based on provider.
 *
 * Currently supports:
 * - 'openrouter': OpenRouter API (300+ models)
 *
 * Future support planned for:
 * - 'openai': Direct OpenAI API
 * - 'anthropic': Direct Anthropic API
 * - 'custom': Custom endpoints
 *
 * Example:
 * ```typescript
 * const llm = createLLM({
 *   provider: 'openrouter',
 *   options: {
 *     apiKey: 'your-api-key',
 *     defaultModel: 'anthropic/claude-3.5-sonnet'
 *   }
 * });
 *
 * const response = await llm.generate('Hello!');
 * ```
 *
 * @param config - The LLM configuration including provider and options
 * @returns An LLM instance implementing ILLM
 */
export function createLLM(config: CreateLLMOptions): ILLM {
  switch (config.provider) {
    case 'openrouter':
      return new OpenRouterLLM(config.options as OpenRouterLLMOptions);

    case 'openai':
      // TODO: Implement OpenAI direct support
      throw new Error(
        'OpenAI direct support not yet implemented. ' +
          'Use OpenRouter with openai/* models instead.'
      );

    case 'anthropic':
      // TODO: Implement Anthropic direct support
      throw new Error(
        'Anthropic direct support not yet implemented. ' +
          'Use OpenRouter with anthropic/* models instead.'
      );

    case 'custom':
      // TODO: Implement custom endpoint support
      throw new Error('Custom LLM endpoints not yet implemented.');

    default:
      throw new Error(`Unknown LLM provider: ${config.provider}`);
  }
}

/**
 * Create an OpenRouter LLM instance directly.
 *
 * This is a convenience function for the most common use case.
 *
 * Example:
 * ```typescript
 * const llm = createOpenRouterLLM({
 *   apiKey: 'your-api-key',
 *   defaultModel: 'openai/gpt-4o'
 * });
 * ```
 *
 * @param options - OpenRouter-specific options
 * @returns An OpenRouterLLM instance
 */
export function createOpenRouterLLM(options: OpenRouterLLMOptions): OpenRouterLLM {
  return new OpenRouterLLM(options);
}

/**
 * LLM class that extends OpenRouterLLM for backwards compatibility
 * and provides a simple default LLM implementation.
 *
 * Example:
 * ```typescript
 * const llm = new LLM({
 *   apiKey: 'your-api-key',
 *   defaultModel: 'anthropic/claude-3.5-sonnet'
 * });
 * ```
 */
export class LLM extends OpenRouterLLM {
  constructor(options: OpenRouterLLMOptions) {
    super(options);
  }
}
