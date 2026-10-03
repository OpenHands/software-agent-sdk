import { HttpClient, HttpError } from './http-client';
import { getCachedAgentServerInfo } from './agent-server-compatibility';
import type { ServerInfo } from '../types/base';
import type {
  AgentServerPromptEnhancementAvailabilityResponse,
  AgentServerPromptEnhancementErrorCode,
  AgentServerPromptEnhancementRequest,
  AgentServerPromptEnhancementResponse,
} from '../models/agent-server-api';

export const PROMPT_ENHANCEMENT_CAPABILITY = 'prompt_enhancement_v1';

export interface PromptEnhancementClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export interface PromptEnhancementRequestOptions {
  signal?: AbortSignal;
}

export type PromptEnhancementAvailability =
  | { available: true }
  | {
      available: false;
      code: 'unsupported_backend' | AgentServerPromptEnhancementErrorCode;
      message: string;
    };

export class PromptEnhancementUnavailableError extends Error {
  readonly code = 'PROMPT_ENHANCEMENT_UNAVAILABLE';

  constructor(message: string) {
    super(message);
    this.name = 'PromptEnhancementUnavailableError';
    Object.setPrototypeOf(this, PromptEnhancementUnavailableError.prototype);
  }
}

/**
 * Client for standalone draft enhancement. The server resolves profile
 * credentials and makes one bounded LLM call without a conversation or tools.
 */
export class PromptEnhancementClient {
  public readonly host: string;
  private readonly client: HttpClient;

  constructor(options: PromptEnhancementClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: options.apiKey,
      timeout: options.timeout ?? 60000,
    });
  }

  /**
   * Check server capability and whether the selected saved profile resolves.
   * This does not contact the model provider, so it cannot guarantee provider
   * availability or credential validity.
   */
  async checkAvailability(profileName: string): Promise<PromptEnhancementAvailability> {
    const serverInfo = await getCachedAgentServerInfo(this.client);
    if (!serverInfo.capabilities?.includes(PROMPT_ENHANCEMENT_CAPABILITY)) {
      return {
        available: false,
        code: 'unsupported_backend',
        message: 'This Agent Server does not support prompt enhancement.',
      };
    }

    try {
      const response = await this.client.get<AgentServerPromptEnhancementAvailabilityResponse>(
        `/api/prompt-enhancement/availability/${encodeURIComponent(profileName)}`
      );
      if (response.data.available) {
        return { available: true };
      }
      return {
        available: false,
        code: response.data.code ?? 'unsupported_configuration',
        message: response.data.message ?? 'The selected profile is unavailable.',
      };
    } catch (error) {
      const detail = error instanceof HttpError ? error.response : undefined;
      if (isPromptEnhancementError(detail)) {
        return {
          available: false,
          code: detail.code,
          message: detail.message,
        };
      }
      throw error;
    }
  }

  /**
   * Enhance a text draft using a server-managed profile. Aborting rejects this
   * client request promptly; a disconnected client may not cancel the upstream
   * provider call, which is independently bounded by the server timeout.
   */
  async enhancePrompt(
    request: AgentServerPromptEnhancementRequest,
    options: PromptEnhancementRequestOptions = {}
  ): Promise<AgentServerPromptEnhancementResponse> {
    const serverInfo = options.signal
      ? (await this.client.get<ServerInfo>('/server_info', { signal: options.signal })).data
      : await getCachedAgentServerInfo(this.client);
    if (!serverInfo.capabilities?.includes(PROMPT_ENHANCEMENT_CAPABILITY)) {
      throw new PromptEnhancementUnavailableError(
        'This Agent Server does not support prompt enhancement.'
      );
    }
    const response = await this.client.post<AgentServerPromptEnhancementResponse>(
      '/api/prompt-enhancement/enhance',
      request,
      { signal: options.signal }
    );
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}

const PROMPT_ENHANCEMENT_ERROR_CODES = new Set<AgentServerPromptEnhancementErrorCode>([
  'empty_input',
  'input_too_large',
  'output_too_large',
  'invalid_model_output',
  'profile_not_found',
  'profile_unavailable',
  'unsupported_configuration',
  'profile_store_timeout',
  'enhancement_timeout',
  'provider_error',
]);

function isPromptEnhancementError(
  value: unknown
): value is { code: AgentServerPromptEnhancementErrorCode; message: string } {
  return (
    typeof value === 'object' &&
    value !== null &&
    'code' in value &&
    typeof value.code === 'string' &&
    PROMPT_ENHANCEMENT_ERROR_CODES.has(value.code as AgentServerPromptEnhancementErrorCode) &&
    'message' in value &&
    typeof value.message === 'string'
  );
}
