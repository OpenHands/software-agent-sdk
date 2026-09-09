/**
 * Remote workspace implementation for executing commands and file operations
 *
 * This implements the IWorkspace interface by connecting to a remote OpenHands
 * agent server. It mirrors the Python SDK's RemoteWorkspace class.
 */

import { BashClient } from '../client/bash-client';
import { HttpClient } from '../client/http-client';
import {
  CommandResult,
  FileOperationResult,
  FileDownloadResult,
  GitChange,
  GitDiff,
} from '../models/workspace';
import { ConversationID } from '../types/base';
import { IWorkspace, BaseWorkspaceOptions, GitQueryOptions } from './base';

/**
 * Options for creating a RemoteWorkspace instance.
 */
export interface RemoteWorkspaceOptions extends BaseWorkspaceOptions {
  /** The remote host URL for the workspace (e.g., 'http://localhost:8000') */
  host: string;
  /** API key for authenticating with the remote host (optional) */
  apiKey?: string;
}

/**
 * Remote workspace implementation that connects to an OpenHands agent server.
 *
 * RemoteWorkspace provides access to a sandboxed environment running on a remote
 * OpenHands agent server. This is the recommended approach for production deployments
 * as it provides better isolation and security.
 */
export class RemoteWorkspace implements IWorkspace {
  public readonly host: string;
  public readonly workingDir: string;
  public readonly apiKey?: string;
  public readonly client: HttpClient;
  public readonly bash: BashClient;

  constructor(options: RemoteWorkspaceOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.workingDir = options.workingDir;
    this.apiKey = options.apiKey;

    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: 60000,
    });

    this.bash = new BashClient({
      host: this.host,
      ...(this.apiKey ? { apiKey: this.apiKey } : {}),
    });
  }

  /**
   * Start a workspace static-asset session for a conversation and return the
   * base URL its workspace files are served from.
   *
   * Calls `POST /api/auth/workspace-session` to exchange the workspace's
   * configured `X-Session-API-Key` for an HttpOnly cookie
   * (`oh_workspace_session_key`) scoped to `/api/conversations`. With the
   * cookie set, browsers can embed workspace artifacts directly as
   * `<iframe src>`, `<img src>` or top-level navigations without having to
   * attach the custom `X-Session-API-Key` header — which they cannot do on
   * those request types.
   *
   * The fetch is made with `credentials: 'include'` so the browser persists
   * the `Set-Cookie` response even when the agent server lives on a different
   * origin from the embedding page (e.g. canvas + `agent-{id}.example.com`).
   *
   * This is intentionally a workspace-level method rather than a
   * conversation-level one: the static asset server lives on the agent host
   * and consumers (e.g. agent-canvas) often want to embed workspace files
   * before they have a full `RemoteConversation` constructed — they just need
   * a conversation ID.
   *
   * @param conversationId The conversation whose workspace files should be
   *                       served from the returned URL.
   * @returns The base URL for the workspace static file server, including a
   *          trailing slash (e.g. `https://host/api/conversations/{id}/workspace/`),
   *          suitable for joining a relative path onto.
   */
  async startWorkspaceSession(conversationId: ConversationID): Promise<string> {
    await this.client.post('/api/auth/workspace-session', undefined, {
      credentials: 'include',
    });
    return `${this.host}/api/conversations/${conversationId}/workspace/`;
  }

  async deleteWorkspaceSession(): Promise<void> {
    await this.client.delete('/api/auth/workspace-session', {
      credentials: 'include',
      acceptableStatusCodes: new Set([204]),
    });
  }

  async executeCommand(
    command: string,
    cwd?: string,
    timeout: number = 30.0
  ): Promise<CommandResult> {
    const payload: Record<string, unknown> = {
      command,
      timeout: Math.floor(timeout),
    };

    if (cwd) {
      payload.cwd = cwd;
    }

    const response = await this.client.post('/api/bash/execute_bash_command', payload, {
      timeout: (timeout + 10) * 1000,
    });

    const bashOutput = response.data as { exit_code?: number; stdout?: string; stderr?: string };

    return {
      command,
      exit_code: bashOutput.exit_code ?? 0,
      stdout: bashOutput.stdout || '',
      stderr: bashOutput.stderr || '',
      timeout_occurred: false,
    };
  }

  async fileUpload(
    content: string | Blob | File,
    destinationPath: string,
    fileName?: string
  ): Promise<FileOperationResult> {
    const formData = new FormData();

    let blob: Blob;
    let finalFileName: string;

    if (content instanceof File) {
      blob = content;
      finalFileName = fileName || content.name;
    } else if (content instanceof Blob) {
      blob = content;
      finalFileName = fileName || 'blob-file';
    } else {
      blob = new Blob([content], { type: 'text/plain' });
      finalFileName = fileName || 'text-file.txt';
    }

    formData.append('file', blob, finalFileName);

    const response = await this.client.request({
      method: 'POST',
      url: '/api/file/upload',
      params: { path: destinationPath },
      data: formData,
      timeout: 60000,
    });

    const resultData = response.data as { success?: boolean; file_size?: number; error?: string };

    return {
      success: resultData.success ?? true,
      source_path: finalFileName,
      destination_path: destinationPath,
      file_size: resultData.file_size,
      error: resultData.error,
    };
  }

  async fileDownload(sourcePath: string): Promise<FileDownloadResult> {
    const response = await this.client.get('/api/file/download', {
      params: { path: sourcePath },
      timeout: 60000,
    });

    let content: string | Blob;
    let fileSize: number;

    if (typeof response.data === 'string') {
      content = response.data;
      fileSize = new Blob([response.data]).size;
    } else if (response.data instanceof ArrayBuffer) {
      content = new Blob([response.data]);
      fileSize = response.data.byteLength;
    } else if (response.data instanceof Blob) {
      content = response.data;
      fileSize = response.data.size;
    } else {
      const stringData = JSON.stringify(response.data);
      content = stringData;
      fileSize = new Blob([stringData]).size;
    }

    return {
      success: true,
      source_path: sourcePath,
      content,
      file_size: fileSize,
    };
  }

  async gitChanges(path: string, options: GitQueryOptions = {}): Promise<GitChange[]> {
    const params: Record<string, string> = { path };
    if (options.ref !== undefined) {
      params.ref = options.ref;
    }
    try {
      const response = await this.client.get<GitChange[]>('/api/git/changes', {
        params,
      });
      return response.data;
    } catch (error) {
      throw new Error(
        `Failed to get git changes: ${error instanceof Error ? error.message : String(error)}`,
        { cause: error }
      );
    }
  }

  async gitDiff(path: string, options: GitQueryOptions = {}): Promise<GitDiff> {
    const params: Record<string, string> = { path };
    if (options.ref !== undefined) {
      params.ref = options.ref;
    }
    try {
      const response = await this.client.get<GitDiff>('/api/git/diff', {
        params,
      });
      return response.data;
    } catch (error) {
      throw new Error(
        `Failed to get git diff: ${error instanceof Error ? error.message : String(error)}`,
        { cause: error }
      );
    }
  }

  /**
   * Convenience method to upload text content as a file
   */
  async uploadText(
    text: string,
    destinationPath: string,
    fileName?: string
  ): Promise<FileOperationResult> {
    return this.fileUpload(text, destinationPath, fileName);
  }

  /**
   * Convenience method to upload a File object (from file input)
   */
  async uploadFileObject(file: File, destinationPath: string): Promise<FileOperationResult> {
    return this.fileUpload(file, destinationPath);
  }

  /**
   * Convenience method to download file content as text
   */
  async downloadAsText(sourcePath: string): Promise<string> {
    const result = await this.fileDownload(sourcePath);
    if (!result.success) {
      throw new Error(result.error || 'Download failed');
    }

    if (typeof result.content === 'string') {
      return result.content;
    } else if (result.content instanceof Blob) {
      return await result.content.text();
    }

    return '';
  }

  /**
   * Convenience method to download file content as a Blob
   */
  async downloadAsBlob(sourcePath: string): Promise<Blob> {
    const result = await this.fileDownload(sourcePath);
    if (!result.success) {
      throw new Error(result.error || 'Download failed');
    }

    if (result.content instanceof Blob) {
      return result.content;
    } else if (typeof result.content === 'string') {
      return new Blob([result.content], { type: 'text/plain' });
    }

    return new Blob();
  }

  /**
   * Convenience method to trigger a browser download of a file
   */
  async downloadAndSave(sourcePath: string, saveAsFileName?: string): Promise<void> {
    const blob = await this.downloadAsBlob(sourcePath);

    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = saveAsFileName || sourcePath.split('/').pop() || 'download';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  close(): void {
    this.bash.close();
    this.client.close();
  }
}
