import { HttpClient } from './http-client';
import type {
  InstallPluginRequest,
  InstalledPluginInfo,
  InstalledPluginsResponse,
  MarketplaceCatalogResponse,
  PluginActionResponse,
  PluginsRequest,
  PluginsResponse,
  RefreshPluginResponse,
  TogglePluginResponse,
} from '../models/api';

export interface PluginsClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export class PluginsClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: PluginsClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  async getPlugins(request: PluginsRequest = {}): Promise<PluginsResponse> {
    const response = await this.client.post<PluginsResponse>('/api/plugins', request);
    return response.data;
  }

  async getPluginsMarketplace(): Promise<MarketplaceCatalogResponse> {
    const response = await this.client.get<MarketplaceCatalogResponse>('/api/plugins/marketplace');
    return response.data;
  }

  async installPlugin(request: InstallPluginRequest): Promise<InstalledPluginInfo> {
    const response = await this.client.post<InstalledPluginInfo>('/api/plugins/install', request);
    return response.data;
  }

  async listInstalledPlugins(): Promise<InstalledPluginsResponse> {
    const response = await this.client.get<InstalledPluginsResponse>('/api/plugins/installed');
    return response.data;
  }

  async getInstalledPlugin(pluginName: string): Promise<InstalledPluginInfo> {
    const response = await this.client.get<InstalledPluginInfo>(
      `/api/plugins/installed/${encodeURIComponent(pluginName)}`
    );
    return response.data;
  }

  async setPluginEnabled(pluginName: string, enabled: boolean): Promise<TogglePluginResponse> {
    const response = await this.client.patch<TogglePluginResponse>(
      `/api/plugins/installed/${encodeURIComponent(pluginName)}`,
      { enabled }
    );
    return response.data;
  }

  async uninstallPlugin(pluginName: string): Promise<PluginActionResponse> {
    const response = await this.client.delete<PluginActionResponse>(
      `/api/plugins/installed/${encodeURIComponent(pluginName)}`
    );
    return response.data;
  }

  async refreshPlugin(pluginName: string): Promise<RefreshPluginResponse> {
    const response = await this.client.post<RefreshPluginResponse>(
      `/api/plugins/installed/${encodeURIComponent(pluginName)}/refresh`
    );
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
