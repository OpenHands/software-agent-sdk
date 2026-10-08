import { HttpClient } from './http-client';
import type { CodexAuthStatus, CodexDeviceChallenge } from '../models/api';
import type { LLMMetadataClientOptions } from './llm-client';

const AUTH_PATH = '/api/acp/codex/auth';

/** Server-owned ChatGPT authentication. This client never handles OAuth tokens. */
export class CodexAuthClient {
  private readonly client: HttpClient;

  constructor(options: LLMMetadataClientOptions) {
    this.client = new HttpClient({
      baseUrl: options.host.replace(/\/$/, ''),
      apiKey: options.apiKey,
      timeout: options.timeout ?? 60000,
    });
  }

  async getStatus(): Promise<CodexAuthStatus> {
    return (await this.client.get<CodexAuthStatus>(`${AUTH_PATH}/status`)).data;
  }

  async startDeviceLogin(): Promise<CodexDeviceChallenge> {
    return (await this.client.post<CodexDeviceChallenge>(`${AUTH_PATH}/device/start`)).data;
  }

  async pollDeviceLogin(deviceCode: string): Promise<CodexAuthStatus> {
    return (
      await this.client.post<CodexAuthStatus>(`${AUTH_PATH}/device/poll`, {
        device_code: deviceCode,
      })
    ).data;
  }

  async cancelDeviceLogin(deviceCode: string): Promise<CodexAuthStatus> {
    return (
      await this.client.post<CodexAuthStatus>(`${AUTH_PATH}/device/cancel`, {
        device_code: deviceCode,
      })
    ).data;
  }

  async logout(): Promise<CodexAuthStatus> {
    return (await this.client.post<CodexAuthStatus>(`${AUTH_PATH}/logout`)).data;
  }

  close(): void {
    this.client.close();
  }
}
