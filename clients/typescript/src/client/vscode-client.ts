import { createRuntimeHttpClients } from './runtime-transport';
import type { RuntimeServiceClientOptions } from './runtime-transport';
import type { HttpClient } from './http-client';
import { VSCodeStatusResponse, VSCodeUrlResponse } from '../models/api';

export type VSCodeClientOptions = RuntimeServiceClientOptions;

export interface GetVSCodeUrlOptions {
  baseUrl?: string;
  workspaceDir?: string;
}

/**
 * Client for interacting with the legacy built-in VSCode server endpoints.
 *
 * @deprecated Deprecated since v1.50.1 and scheduled for removal in v1.55.0.
 * Built-in OpenVSCode has been removed from default Agent Server images;
 * use the standalone VSCode App extension instead.
 */
export class VSCodeClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: VSCodeClientOptions) {
    const { runtimeClient } = createRuntimeHttpClients(options);
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = runtimeClient;
  }

  /**
   * Get the VSCode URL with authentication token.
   *
   * @deprecated Deprecated since v1.50.1 and scheduled for removal in v1.55.0.
   */
  async getUrl(options: GetVSCodeUrlOptions = {}): Promise<string | null> {
    const response = await this.client.get<VSCodeUrlResponse>('/api/vscode/url', {
      params: {
        ...(options.baseUrl ? { base_url: options.baseUrl } : {}),
        ...(options.workspaceDir ? { workspace_dir: options.workspaceDir } : {}),
      },
    });
    return response.data.url;
  }

  /**
   * Get the VSCode server status.
   *
   * @deprecated Deprecated since v1.50.1 and scheduled for removal in v1.55.0.
   */
  async getStatus(): Promise<VSCodeStatusResponse> {
    const response = await this.client.get<VSCodeStatusResponse>('/api/vscode/status');
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
