import { HttpClient, HttpError } from './http-client';
import {
  BashCommand,
  BashEvent,
  BashEventPage,
  BashEventSearchOptions,
  BashOutput,
  ClearBashEventsResponse,
  ExecuteBashRequest,
} from '../models/workspace';

export interface BashClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export class BashClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: BashClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  async searchEvents(options: BashEventSearchOptions = {}): Promise<BashEventPage> {
    const response = await this.client.get<BashEventPage>('/api/bash/bash_events/search', {
      params: options as Record<string, unknown>,
    });
    return response.data;
  }

  async getEvent(eventId: string): Promise<BashEvent> {
    const response = await this.client.get<BashEvent>(`/api/bash/bash_events/${eventId}`);
    return response.data;
  }

  async getEvents(eventIds: string[]): Promise<Array<BashEvent | null>> {
    return Promise.all(
      eventIds.map(async (eventId) => {
        try {
          return await this.getEvent(eventId);
        } catch (error) {
          if (error instanceof HttpError && error.status === 404) {
            return null;
          }
          throw error;
        }
      })
    );
  }

  async startCommand(
    request: string | ExecuteBashRequest,
    cwd?: string,
    timeout?: number
  ): Promise<BashCommand> {
    const payload = this.normalizeRequest(request, cwd, timeout);
    const response = await this.client.post<BashCommand>('/api/bash/start_bash_command', payload);
    return response.data;
  }

  async executeCommand(
    request: string | ExecuteBashRequest,
    cwd?: string,
    timeout?: number
  ): Promise<BashOutput> {
    const payload = this.normalizeRequest(request, cwd, timeout);
    const response = await this.client.post<BashOutput>('/api/bash/execute_bash_command', payload, {
      timeout: ((payload.timeout || 30) + 10) * 1000,
    });
    return response.data;
  }

  async clearEvents(): Promise<ClearBashEventsResponse> {
    const response = await this.client.delete<ClearBashEventsResponse>('/api/bash/bash_events');
    return response.data;
  }

  close(): void {
    this.client.close();
  }

  private normalizeRequest(
    request: string | ExecuteBashRequest,
    cwd?: string,
    timeout?: number
  ): ExecuteBashRequest {
    if (typeof request === 'string') {
      return {
        command: request,
        ...(cwd ? { cwd } : {}),
        ...(timeout !== undefined ? { timeout: Math.floor(timeout) } : {}),
      };
    }

    return {
      ...request,
      ...(request.timeout !== undefined ? { timeout: Math.floor(request.timeout) } : {}),
    };
  }
}
