import { HttpClient } from './http-client';
import type {
  FileHomeResponse,
  FileSearchSubdirsOptions,
  FileSubdirectoryPage,
} from '../models/api';

export interface FileClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export class FileClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: FileClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  async searchSubdirectories(
    path: string,
    options: FileSearchSubdirsOptions = {}
  ): Promise<FileSubdirectoryPage> {
    const response = await this.client.get<FileSubdirectoryPage>('/api/file/search_subdirs', {
      params: {
        path,
        page_id: options.pageId,
        limit: options.limit,
      },
    });
    return response.data;
  }

  async getHome(): Promise<FileHomeResponse> {
    const response = await this.client.get<FileHomeResponse>('/api/file/home');
    return response.data;
  }

  async downloadFile(path: string): Promise<ArrayBuffer> {
    const response = await this.client.get<ArrayBuffer>('/api/file/download', {
      params: { path },
      responseType: 'arrayBuffer',
    });
    return response.data;
  }

  async downloadTextFile(path: string): Promise<string> {
    return new TextDecoder().decode(await this.downloadFile(path));
  }

  async downloadTrajectory(conversationId: string): Promise<Blob> {
    const response = await this.client.get<Blob>(
      `/api/file/download-trajectory/${encodeURIComponent(conversationId)}`,
      { responseType: 'blob' }
    );
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
