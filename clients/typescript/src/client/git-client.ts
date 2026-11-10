import { HttpClient } from './http-client';
import type { GitProviderRepositoryPage } from '../models/api';

export interface GitClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export interface SearchRepositoriesOptions {
  provider: string;
  query?: string;
  limit?: number;
  pageId?: string;
}

export class GitClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: GitClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
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

  close(): void {
    this.client.close();
  }
}
