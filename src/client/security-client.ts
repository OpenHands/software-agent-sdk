import { HttpClient } from './http-client';
import type { SecuritySettings, SecurityTraceResponse } from '../models/api';

export interface SecurityClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export class SecurityClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: SecurityClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  async getPolicy(): Promise<string> {
    const response = await this.client.get<{ policy: string }>('/api/security/policy');
    return response.data.policy;
  }

  async updatePolicy(policy: string): Promise<void> {
    await this.client.post('/api/security/policy', { policy });
  }

  async getSettings(): Promise<SecuritySettings> {
    const response = await this.client.get<SecuritySettings>('/api/security/settings');
    return response.data;
  }

  async getRiskSeverity(): Promise<number> {
    return (await this.getSettings()).RISK_SEVERITY;
  }

  async updateSettings(settings: Partial<SecuritySettings>): Promise<void> {
    await this.client.post('/api/security/settings', settings);
  }

  async updateRiskSeverity(riskSeverity: number): Promise<void> {
    await this.updateSettings({ RISK_SEVERITY: riskSeverity });
  }

  async exportTrace(): Promise<SecurityTraceResponse> {
    const response = await this.client.get<SecurityTraceResponse>('/api/security/export-trace');
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
