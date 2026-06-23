import { HttpClient, HttpError } from './http-client';
import { ConversationExecutionStatus, LLM, Success } from '../types/base';
import type {
  AgentResponseResult,
  AskAgentResponse,
  ConfirmationResponseRequest,
  ConversationEvent,
  ConversationEventCountOptions,
  ConversationEventPage,
  ConversationEventSearchOptions,
  ConversationInfo,
  ConversationSearchRequest,
  ConversationSearchResponse,
  ForkConversationRequest,
  SetConfirmationPolicyRequest,
  SetSecurityAnalyzerRequest,
  StartGoalRequest,
  UpdateConversationRequest,
  UpdateSecretsRequest,
} from '../models/conversation';
import type { ConfirmationPolicyBase } from '../types/base';

export interface ConversationClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

/**
 * Payload for creating a new conversation.
 *
 * Mutually exclusive sources for the agent:
 * - `agent_profile_id` — stable UUID; server resolves the profile server-side.
 * - `agent` / `agent_settings` — direct agent spec (legacy path).
 *
 * Additional fields (e.g. `initial_message`, `max_iterations`) are forwarded
 * as-is. `agent_profile_id` support lands with SDK PR #3784.
 */
export interface CreateConversationPayload {
  /** Stable UUID of an agent profile to resolve server-side. */
  agent_profile_id?: string;
  agent?: unknown;
  agent_settings?: unknown;
  [key: string]: unknown;
}

export interface SendConversationEventOptions {
  run?: boolean;
}

export class ConversationClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: ConversationClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  async createConversation<TConversation = ConversationInfo>(
    payload: CreateConversationPayload
  ): Promise<TConversation> {
    const response = await this.client.post<TConversation>('/api/conversations', payload);
    return response.data;
  }

  async searchConversations(
    options: ConversationSearchRequest = {}
  ): Promise<ConversationSearchResponse> {
    const response = await this.client.get<ConversationSearchResponse>(
      '/api/conversations/search',
      {
        params: options as Record<string, unknown>,
      }
    );
    return response.data;
  }

  async countConversations(
    options: { status?: ConversationExecutionStatus } = {}
  ): Promise<number> {
    const response = await this.client.get<number>('/api/conversations/count', {
      params: options as Record<string, unknown>,
    });
    return response.data;
  }

  async getConversations<TConversation = ConversationInfo>(
    conversationIds: string[]
  ): Promise<Array<TConversation | null>> {
    const response = await this.client.get<Array<TConversation | null>>('/api/conversations', {
      params: { ids: conversationIds },
    });
    return response.data;
  }

  async getConversation<TConversation = ConversationInfo>(
    conversationId: string
  ): Promise<TConversation> {
    const response = await this.client.get<TConversation>(`/api/conversations/${conversationId}`);
    return response.data;
  }

  async searchEvents(
    conversationId: string,
    options: ConversationEventSearchOptions = {}
  ): Promise<ConversationEventPage> {
    const response = await this.client.get<ConversationEventPage>(
      `/api/conversations/${conversationId}/events/search`,
      { params: options as Record<string, unknown> }
    );
    return response.data;
  }

  async getEvent(conversationId: string, eventId: string): Promise<ConversationEvent> {
    const response = await this.client.get<ConversationEvent>(
      `/api/conversations/${conversationId}/events/${eventId}`
    );
    return response.data;
  }

  async getEvents(
    conversationId: string,
    eventIds: string[]
  ): Promise<Array<ConversationEvent | null>> {
    return Promise.all(
      eventIds.map(async (eventId) => {
        try {
          return await this.getEvent(conversationId, eventId);
        } catch (error) {
          if (error instanceof HttpError && error.status === 404) {
            return null;
          }
          throw error;
        }
      })
    );
  }

  async sendEvent(
    conversationId: string,
    event: object,
    options: SendConversationEventOptions = {}
  ): Promise<void> {
    await this.client.post(`/api/conversations/${conversationId}/events`, {
      ...event,
      ...(options.run === undefined ? {} : { run: options.run }),
    });
  }

  async pauseConversation(conversationId: string): Promise<Success> {
    const response = await this.client.post<Success>(
      `/api/conversations/${conversationId}/pause`,
      {}
    );
    return response.data;
  }

  async interruptConversation(conversationId: string): Promise<Success> {
    const response = await this.client.post<Success>(
      `/api/conversations/${conversationId}/interrupt`,
      {}
    );
    return response.data;
  }

  async runConversation(conversationId: string): Promise<Success> {
    const response = await this.client.post<Success>(
      `/api/conversations/${conversationId}/run`,
      {}
    );
    return response.data;
  }

  /**
   * Start a `/goal` loop inside an existing conversation. The loop runs in the
   * conversation's own history and event stream (it does not fork). The server
   * returns 409 if a run or goal loop is already active and 400 for an invalid
   * objective (e.g. empty); a missing conversation yields 404.
   */
  async startGoal(conversationId: string, request: StartGoalRequest): Promise<Success> {
    const response = await this.client.post<Success>(
      `/api/conversations/${conversationId}/goal`,
      request
    );
    return response.data;
  }

  /**
   * Stop the active `/goal` loop in this conversation. Cancels only the
   * background goal loop (not the conversation) and records an `interrupted`
   * status so {@link resumeGoal} can continue it. Succeeds even when no loop is
   * running; a missing conversation yields 404.
   */
  async stopGoal(conversationId: string): Promise<Success> {
    const response = await this.client.post<Success>(
      `/api/conversations/${conversationId}/goal/stop`,
      {}
    );
    return response.data;
  }

  /**
   * Resume the last interrupted `/goal` loop in this conversation. The server
   * returns 409 if a run or goal loop is already active and 400 when there is
   * no resumable goal (none started, or it already completed); a missing
   * conversation yields 404.
   */
  async resumeGoal(conversationId: string): Promise<Success> {
    const response = await this.client.post<Success>(
      `/api/conversations/${conversationId}/goal/resume`,
      {}
    );
    return response.data;
  }

  async askAgent(conversationId: string, question: string): Promise<AskAgentResponse> {
    const response = await this.client.post<AskAgentResponse>(
      `/api/conversations/${conversationId}/ask_agent`,
      { question }
    );
    return response.data;
  }

  async getEventCount(
    conversationId: string,
    options: ConversationEventCountOptions = {}
  ): Promise<number> {
    const response = await this.client.get<number>(
      `/api/conversations/${conversationId}/events/count`,
      { params: options as Record<string, unknown> }
    );
    return response.data;
  }

  async respondToConfirmation<TResponse = unknown>(
    conversationId: string,
    request: ConfirmationResponseRequest
  ): Promise<TResponse> {
    const response = await this.client.post<TResponse>(
      `/api/conversations/${conversationId}/events/respond_to_confirmation`,
      request
    );
    return response.data;
  }

  async getAgentFinalResponse(conversationId: string): Promise<AgentResponseResult> {
    const response = await this.client.get<AgentResponseResult>(
      `/api/conversations/${conversationId}/agent_final_response`
    );
    return response.data;
  }

  async setConfirmationPolicy(
    conversationId: string,
    policyOrRequest: ConfirmationPolicyBase | SetConfirmationPolicyRequest
  ): Promise<Success> {
    const request =
      'policy' in policyOrRequest ? policyOrRequest : ({ policy: policyOrRequest } as const);
    const response = await this.client.post<Success>(
      `/api/conversations/${conversationId}/confirmation_policy`,
      request
    );
    return response.data;
  }

  async condenseConversation(conversationId: string): Promise<Success> {
    const response = await this.client.post<Success>(
      `/api/conversations/${conversationId}/condense`,
      {}
    );
    return response.data;
  }

  async setSecurityAnalyzer(
    conversationId: string,
    securityAnalyzerOrRequest: unknown | SetSecurityAnalyzerRequest | null
  ): Promise<Success> {
    const request = isSetSecurityAnalyzerRequest(securityAnalyzerOrRequest)
      ? securityAnalyzerOrRequest
      : { security_analyzer: securityAnalyzerOrRequest };
    const response = await this.client.post<Success>(
      `/api/conversations/${conversationId}/security_analyzer`,
      request
    );
    return response.data;
  }

  async updateSecrets(conversationId: string, request: UpdateSecretsRequest): Promise<Success> {
    const response = await this.client.post<Success>(
      `/api/conversations/${conversationId}/secrets`,
      request
    );
    return response.data;
  }

  async forkConversation<TConversation = ConversationInfo>(
    conversationId: string,
    request: ForkConversationRequest = {},
    options: { includeSkills?: boolean } = {}
  ): Promise<TConversation> {
    const response = await this.client.post<TConversation>(
      `/api/conversations/${conversationId}/fork`,
      request,
      {
        params:
          options.includeSkills === undefined
            ? undefined
            : { include_skills: options.includeSkills },
      }
    );
    return response.data;
  }

  async switchProfile(conversationId: string, profileName: string): Promise<void> {
    await this.client.post<Success>(`/api/conversations/${conversationId}/switch_profile`, {
      profile_name: profileName,
    });
  }

  async switchLLM(conversationId: string, llm: LLM): Promise<void> {
    await this.client.post<Success>(`/api/conversations/${conversationId}/switch_llm`, { llm });
  }

  /**
   * Switch the model of a running ACP conversation in place (the ACP analog of
   * {@link switchLLM}). Calls the wrapper's `session/set_model` on the live
   * session; context is preserved. Only valid once the ACP session exists —
   * the server returns 409 before the first message (set the default via
   * settings instead) and 400 for non-ACP conversations.
   */
  async switchAcpModel(conversationId: string, model: string): Promise<void> {
    await this.client.post<Success>(`/api/conversations/${conversationId}/switch_acp_model`, {
      model,
    });
  }

  async deleteConversation(conversationId: string): Promise<void> {
    await this.client.delete<Success>(`/api/conversations/${conversationId}`);
  }

  async updateConversation<TConversation = ConversationInfo>(
    conversationId: string,
    update: UpdateConversationRequest
  ): Promise<TConversation> {
    const response = await this.client.patch<TConversation>(
      `/api/conversations/${conversationId}`,
      update
    );
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}

function isSetSecurityAnalyzerRequest(value: unknown): value is SetSecurityAnalyzerRequest {
  return typeof value === 'object' && value !== null && 'security_analyzer' in value;
}
