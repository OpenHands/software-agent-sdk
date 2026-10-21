import { HttpClient } from './http-client';
import type { SubAgentsRequest, SubAgentsResponse } from '../models/api';

export interface SubAgentsClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

/**
 * Client for the agent-server's sub-agents catalog.
 *
 * A single read endpoint (`POST /api/sub-agents`) listing the file-based and
 * built-in sub-agents available to a workspace. Read-only: it discovers the
 * catalog, it does not mutate it.
 */
export class SubAgentsClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: SubAgentsClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  /**
   * List the file-based and built-in sub-agents for the workspace.
   *
   * Merged first-wins by name with precedence project > user > builtin.
   */
  async getSubAgents(request: SubAgentsRequest = {}): Promise<SubAgentsResponse> {
    const response = await this.client.post<SubAgentsResponse>('/api/sub-agents', request);
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
