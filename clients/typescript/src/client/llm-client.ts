import { HttpClient } from './http-client';
import {
  LLMSubscriptionDevicePollRequest,
  LLMSubscriptionDeviceStartResponse,
  LLMSubscriptionModelsResponse,
  LLMSubscriptionStatusResponse,
  ModelsResponse,
  ProvidersResponse,
  VerifiedModelsResponse,
} from '../models/api';

export interface LLMMetadataClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export class LLMMetadataClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: LLMMetadataClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  async getProviders(): Promise<string[]> {
    const response = await this.client.get<ProvidersResponse>('/api/llm/providers');
    return response.data.providers;
  }

  async getModels(provider?: string): Promise<string[]> {
    const response = await this.client.get<ModelsResponse>('/api/llm/models', {
      params: provider ? { provider } : undefined,
    });
    return response.data.models;
  }

  async getVerifiedModels(): Promise<Record<string, string[]>> {
    const response = await this.client.get<VerifiedModelsResponse>('/api/llm/models/verified');
    return response.data.models;
  }

  async getOpenAISubscriptionModels(): Promise<string[]> {
    const response = await this.client.get<LLMSubscriptionModelsResponse>(
      '/api/llm/subscription/openai/models'
    );
    return response.data.models;
  }

  async getOpenAISubscriptionStatus(): Promise<LLMSubscriptionStatusResponse> {
    const response = await this.client.get<LLMSubscriptionStatusResponse>(
      '/api/llm/subscription/openai/status'
    );
    return response.data;
  }

  async startOpenAISubscriptionDeviceLogin(): Promise<LLMSubscriptionDeviceStartResponse> {
    const response = await this.client.post<LLMSubscriptionDeviceStartResponse>(
      '/api/llm/subscription/openai/device/start'
    );
    return response.data;
  }

  async pollOpenAISubscriptionDeviceLogin(
    deviceCode: string
  ): Promise<LLMSubscriptionStatusResponse> {
    const body: LLMSubscriptionDevicePollRequest = { device_code: deviceCode };
    const response = await this.client.post<LLMSubscriptionStatusResponse>(
      '/api/llm/subscription/openai/device/poll',
      body
    );
    return response.data;
  }

  async logoutOpenAISubscription(): Promise<LLMSubscriptionStatusResponse> {
    const response = await this.client.post<LLMSubscriptionStatusResponse>(
      '/api/llm/subscription/openai/logout'
    );
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
