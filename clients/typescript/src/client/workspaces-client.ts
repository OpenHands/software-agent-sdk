import {
  AgentServerFeatureRequirements,
  assertAgentServerSupports,
} from './agent-server-compatibility';
import { HttpClient } from './http-client';

export interface WorkspacesClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export interface WorkspaceItem {
  id: string;
  name: string;
  path: string;
  parentPath?: string | null;
}

export interface WorkspaceParentItem {
  id: string;
  name: string;
  path: string;
}

export interface WorkspacesListResponse {
  workspaces: WorkspaceItem[];
  workspaceParents: WorkspaceParentItem[];
}

export interface DeleteWorkspaceResponse {
  deleted: boolean;
}

export class WorkspacesClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: WorkspacesClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  async listWorkspaces(): Promise<WorkspacesListResponse> {
    await this.ensureWorkspacesSupported();
    const response = await this.client.get<WorkspacesListResponse>('/api/workspaces');
    return response.data;
  }

  async addWorkspaces(workspaces: WorkspaceItem[]): Promise<WorkspacesListResponse> {
    await this.ensureWorkspacesSupported();
    const response = await this.client.post<WorkspacesListResponse>('/api/workspaces', {
      workspaces,
    });
    return response.data;
  }

  async deleteWorkspace(path: string): Promise<DeleteWorkspaceResponse> {
    await this.ensureWorkspacesSupported();
    const response = await this.client.delete<DeleteWorkspaceResponse>('/api/workspaces', {
      params: { path },
    });
    return response.data;
  }

  async addWorkspaceParents(parents: WorkspaceParentItem[]): Promise<WorkspacesListResponse> {
    await this.ensureWorkspacesSupported();
    const response = await this.client.post<WorkspacesListResponse>('/api/workspaces/parents', {
      parents,
    });
    return response.data;
  }

  async deleteWorkspaceParent(path: string): Promise<DeleteWorkspaceResponse> {
    await this.ensureWorkspacesSupported();
    const response = await this.client.delete<DeleteWorkspaceResponse>('/api/workspaces/parents', {
      params: { path },
    });
    return response.data;
  }

  close(): void {
    this.client.close();
  }

  private async ensureWorkspacesSupported(): Promise<void> {
    await assertAgentServerSupports(this.client, AgentServerFeatureRequirements.workspaces);
  }
}
