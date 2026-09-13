import { runtimeServiceConnections } from './runtime-transport';
import type { RuntimeServiceOptions } from './runtime-transport';
import type { HttpClient } from './http-client';
import type {
  FileHomeOptions,
  FileHomeResponse,
  FileSearchSubdirsOptions,
  FileSubdirectoryPage,
} from '../models/api';
import type { Success } from '../types/base';

export type FileClientOptions = RuntimeServiceOptions;

export type FileUploadContent = string | Blob | File;

export class FileClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;
  private readonly server: HttpClient;
  private readonly conversationId?: string;

  constructor(options: FileClientOptions) {
    const { server, runtime } = runtimeServiceConnections(options);
    this.host = server.host;
    this.apiKey = server.sessionApiKey;
    this.client = runtime;
    this.server = server;
    this.conversationId = options.runtimeTransport?.conversationId ?? options.conversationId;
  }

  async searchSubdirectories(
    path: string,
    options: FileSearchSubdirsOptions = {}
  ): Promise<FileSubdirectoryPage> {
    const response = await this.server.get<FileSubdirectoryPage>('/api/file/search_subdirs', {
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
    const response = await this.server.get<FileHomeResponse>('/api/file/home', {
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
    if (this.conversationId !== undefined && conversationId !== this.conversationId) {
      throw new Error('Trajectory must belong to the selected runtime');
    }
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
