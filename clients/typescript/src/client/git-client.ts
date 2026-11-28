import { createRuntimeHttpClients } from './runtime-transport';
import type { RuntimeServiceClientOptions } from './runtime-transport';
import type { HttpClient } from './http-client';
import type { GitProviderRepositoryPage } from '../models/api';
import type {
  AgentServerGitCommitChangesResponse,
  AgentServerGitCommitsResponse,
} from '../models/agent-server-api';

export type GitClientOptions = RuntimeServiceClientOptions;

export interface SearchRepositoriesOptions {
  provider: string;
  query?: string;
  limit?: number;
  pageId?: string;
}

export interface GitCommitsOptions {
  /** Maximum commits to return (server range 1-200, default 50). */
  limit?: number;
}

export class GitClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;
  private readonly runtimeClient: HttpClient;

  constructor(options: GitClientOptions) {
    const { serverClient, runtimeClient } = createRuntimeHttpClients(options);
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = serverClient;
    this.runtimeClient = runtimeClient;
  }

  async searchRepositories(options: SearchRepositoriesOptions): Promise<GitProviderRepositoryPage> {
    const response = await this.client.get<GitProviderRepositoryPage>(
      '/api/git/repositories/search',
      {
        params: {
          provider: options.provider,
          query: options.query,
          limit: options.limit,
          page_id: options.pageId,
        },
      }
    );
    return response.data;
  }

  /** List a repository's most recent commits, newest first. */
  async getCommits(
    path: string,
    options: GitCommitsOptions = {}
  ): Promise<AgentServerGitCommitsResponse> {
    const response = await this.runtimeClient.get<AgentServerGitCommitsResponse>(
      '/api/git/commits',
      { params: { path, limit: options.limit } }
    );
    return response.data;
  }

  /** List the files changed by one commit versus its first parent. */
  async getCommitChanges(path: string, sha: string): Promise<AgentServerGitCommitChangesResponse> {
    const response = await this.runtimeClient.get<AgentServerGitCommitChangesResponse>(
      `/api/git/commits/${encodeURIComponent(sha)}/changes`,
      { params: { path } }
    );
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
