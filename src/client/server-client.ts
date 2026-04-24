import { HttpClient } from './http-client';
import { AliveStatus, ReadyStatus } from '../models/api';
import { ServerInfo } from '../types/base';

export interface ServerClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export class ServerClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: ServerClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  async getRoot<T = unknown>(): Promise<T> {
    const response = await this.client.get<T>('/');
    return response.data;
  }

  async getAlive(): Promise<AliveStatus> {
    const response = await this.client.get<AliveStatus>('/alive');
    return response.data;
  }

  async getHealth(): Promise<string> {
    const response = await this.client.get<string>('/health');
    return response.data;
  }

  async getReady(): Promise<ReadyStatus> {
    const response = await this.client.get<ReadyStatus>('/ready', {
      acceptableStatusCodes: new Set([200, 503]),
    });
    return response.data;
  }

  async getServerInfo(): Promise<ServerInfo> {
    const response = await this.client.get<ServerInfo>('/server_info');
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
