import { HttpClient } from './http-client';
import type {
  InstallSkillRequest,
  InstalledSkillInfo,
  InstalledSkillsResponse,
  MarketplaceResponse,
  RefreshSkillResponse,
  SkillActionResponse,
  SkillsRequest,
  SkillsResponse,
  SyncResponse,
  ToggleSkillResponse,
} from '../models/api';

export interface SkillsClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export class SkillsClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: SkillsClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  async getSkills(request: SkillsRequest = {}): Promise<SkillsResponse> {
    const response = await this.client.post<SkillsResponse>('/api/skills', request);
    return response.data;
  }

  async syncSkills(): Promise<SyncResponse> {
    const response = await this.client.post<SyncResponse>('/api/skills/sync', {});
    return response.data;
  }

  async installSkill(request: InstallSkillRequest): Promise<InstalledSkillInfo> {
    const response = await this.client.post<InstalledSkillInfo>('/api/skills/install', request);
    return response.data;
  }

  async listInstalledSkills(): Promise<InstalledSkillsResponse> {
    const response = await this.client.get<InstalledSkillsResponse>('/api/skills/installed');
    return response.data;
  }

  async getInstalledSkill(skillName: string): Promise<InstalledSkillInfo> {
    const response = await this.client.get<InstalledSkillInfo>(
      `/api/skills/installed/${encodeURIComponent(skillName)}`
    );
    return response.data;
  }

  async toggleSkill(skillName: string, enabled: boolean): Promise<ToggleSkillResponse> {
    const response = await this.client.patch<ToggleSkillResponse>(
      `/api/skills/installed/${encodeURIComponent(skillName)}`,
      { enabled }
    );
    return response.data;
  }

  async uninstallSkill(skillName: string): Promise<SkillActionResponse> {
    const response = await this.client.delete<SkillActionResponse>(
      `/api/skills/installed/${encodeURIComponent(skillName)}`
    );
    return response.data;
  }

  async refreshSkill(skillName: string): Promise<RefreshSkillResponse> {
    const response = await this.client.post<RefreshSkillResponse>(
      `/api/skills/installed/${encodeURIComponent(skillName)}/refresh`
    );
    return response.data;
  }

  async getMarketplace(): Promise<MarketplaceResponse> {
    const response = await this.client.get<MarketplaceResponse>('/api/skills/marketplace');
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
