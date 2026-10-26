import { HttpClient } from './http-client';
import type { ConversationExecutionStatus, Success } from '../types/base';

export interface VoiceClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export interface VoiceAvailability {
  available: boolean;
  run_active: boolean;
  execution_status: ConversationExecutionStatus;
  model?: string;
  provider?: 'openai' | 'codex';
  delegation?: 'client' | 'server';
  reason?:
    | 'missing_openai_api_key'
    | 'codex_not_installed'
    | 'codex_not_signed_in'
    | 'codex_unavailable'
    | null;
}

export interface RealtimeOffer {
  sdp: string;
}

export interface RealtimeAnswer {
  sdp: string;
  call_id?: string | null;
  model?: string;
  provider?: 'openai' | 'codex';
  delegation?: 'client' | 'server';
}

export type CodexVoiceErrorCode = 'request_not_sent' | 'relay_failed' | 'connection_failed';

export interface VoiceTranscript {
  id: string;
  role: 'user' | 'assistant';
  text: string;
}

export interface CodexVoiceStatus {
  provider?: 'codex';
  status: 'listening' | 'thinking' | 'speaking' | 'closed' | 'error';
  transcripts: VoiceTranscript[];
  error?: string | null;
  error_code?: CodexVoiceErrorCode | null;
}

/** Conversation-bound voice setup and call control; media stays in the caller's WebRTC peer. */
export class VoiceClient {
  private readonly client: HttpClient;

  constructor(options: VoiceClientOptions) {
    this.client = new HttpClient({
      baseUrl: options.host,
      apiKey: options.apiKey,
      timeout: options.timeout,
    });
  }

  async availability(conversationId: string, signal?: AbortSignal): Promise<VoiceAvailability> {
    const response = await this.client.get<VoiceAvailability>(
      `/api/conversations/${encodeURIComponent(conversationId)}/voice`,
      { signal }
    );
    return response.data;
  }

  async createCall(
    conversationId: string,
    offer: RealtimeOffer,
    signal?: AbortSignal
  ): Promise<RealtimeAnswer> {
    const response = await this.client.post<RealtimeAnswer>(
      `/api/conversations/${encodeURIComponent(conversationId)}/voice/realtime`,
      offer,
      { signal }
    );
    return response.data;
  }

  /** Poll server-delegated Codex calls; OpenAI call state comes from WebRTC. */
  async getCallStatus(
    conversationId: string,
    callId: string,
    signal?: AbortSignal
  ): Promise<CodexVoiceStatus> {
    const response = await this.client.get<CodexVoiceStatus>(
      `/api/conversations/${encodeURIComponent(conversationId)}/voice/realtime/${encodeURIComponent(callId)}`,
      { signal }
    );
    return response.data;
  }

  /** End speech without interrupting accepted work in the saved conversation. */
  async endCall(conversationId: string, callId: string, signal?: AbortSignal): Promise<Success> {
    const response = await this.client.delete<Success>(
      `/api/conversations/${encodeURIComponent(conversationId)}/voice/realtime/${encodeURIComponent(callId)}`,
      { signal }
    );
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
