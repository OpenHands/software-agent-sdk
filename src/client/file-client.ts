import { HttpClient } from './http-client';
import type {
  FileHomeOptions,
  FileHomeResponse,
  FileSearchSubdirsOptions,
  FileSubdirectoryPage,
} from '../models/api';
import type { Success } from '../types/base';

export interface FileClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export type FileUploadContent = string | Blob | File;

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
        include_hidden: options.includeHidden || undefined,
      },
    });
    return response.data;
  }

  async getHome(options: FileHomeOptions = {}): Promise<FileHomeResponse> {
    const response = await this.client.get<FileHomeResponse>('/api/file/home', {
      params: {
        include_hidden: options.includeHidden || undefined,
      },
    });
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

  async uploadFile(
    content: FileUploadContent,
    destinationPath: string,
    fileName?: string
  ): Promise<Success> {
    const formData = new FormData();
    const fileConstructor = typeof File === 'undefined' ? undefined : File;

    if (fileConstructor && content instanceof fileConstructor) {
      formData.append('file', content, fileName || content.name);
    } else if (content instanceof Blob) {
      formData.append('file', content, fileName || 'blob-file');
    } else {
      formData.append(
        'file',
        new Blob([content], { type: 'text/plain' }),
        fileName || 'text-file.txt'
      );
    }

    const response = await this.client.post<Success>('/api/file/upload', formData, {
      params: { path: destinationPath },
    });
    return response.data;
  }

  async uploadTextFile(text: string, destinationPath: string, fileName?: string): Promise<Success> {
    return this.uploadFile(text, destinationPath, fileName);
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
