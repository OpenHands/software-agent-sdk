import { HttpClient } from './http-client';
import type {
  AgentServerInitRequest,
  AgentServerInitResponse,
  AgentServerInitStatus,
} from '../models/agent-server-api';

export interface InitClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export interface InitializeServerOptions {
  /**
   * Bootstrap credential sent as `X-Init-API-Key`. This is the dormant
   * server's secret key, distinct from the session API key used by other
   * clients; it is never sent as `X-Session-API-Key` by this method.
   */
  initApiKey?: string;
}

/** Typed client for the deferred-init (warm-pool) endpoints. */
export class InitClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: InitClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  async getStatus(): Promise<AgentServerInitStatus> {
    const response = await this.client.get<AgentServerInitStatus>('/api/init');
    return response.data;
  }

  async initialize(
    request: AgentServerInitRequest,
    options: InitializeServerOptions = {}
  ): Promise<AgentServerInitResponse> {
    const response = await this.client.post<AgentServerInitResponse>('/api/init', request, {
      headers: options.initApiKey ? { 'X-Init-API-Key': options.initApiKey } : undefined,
    });
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
