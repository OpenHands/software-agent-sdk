/**
 * Remote workspace implementation for executing commands and file operations
 */

import { HttpClient } from '../client/http-client';
import {
  CommandResult,
  FileOperationResult,
  FileDownloadResult,
  GitChange,
  GitDiff,
} from '../models/workspace';

export interface RemoteWorkspaceOptions {
  host: string;
  workingDir: string;
  apiKey?: string;
}

export class RemoteWorkspace {
  public readonly host: string;
  public readonly workingDir: string;
  public readonly apiKey?: string;
  public readonly client: HttpClient;

  constructor(options: RemoteWorkspaceOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.workingDir = options.workingDir;
    this.apiKey = options.apiKey;

    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: 60000,
    });
  }

  async executeCommand(
    command: string,
    cwd?: string,
    timeout: number = 30.0
  ): Promise<CommandResult> {
    console.debug(`Executing remote command: ${command}`);

    try {
      const payload: any = {
        command,
        timeout: Math.floor(timeout),
      };

      if (cwd) {
        payload.cwd = cwd;
      }

      // The API waits for the command to complete and returns the result directly
      const response = await this.client.post('/api/bash/execute_bash_command', payload, {
        timeout: (timeout + 10) * 1000, // Add buffer for network latency
      });

      const bashOutput = response.data;

      return {
        command,
        exit_code: bashOutput.exit_code ?? 0,
        stdout: bashOutput.stdout || '',
        stderr: bashOutput.stderr || '',
        timeout_occurred: false,
      };
    } catch (error) {
      console.error(`Remote command execution failed: ${error}`);
      return {
        command,
        exit_code: -1,
        stdout: '',
        stderr: `Remote execution error: ${error instanceof Error ? error.message : String(error)}`,
        timeout_occurred: false,
      };
    }
  }

  async fileUpload(
    content: string | Blob | File,
    destinationPath: string,
    fileName?: string
  ): Promise<FileOperationResult> {
    console.debug(`Remote file upload to: ${destinationPath}`);

    try {
      // Create FormData for file upload
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
        // String content
        blob = new Blob([content], { type: 'text/plain' });
        finalFileName = fileName || 'text-file.txt';
      }

      formData.append('file', blob, finalFileName);

      // The API expects the path in the URL, not in the form data
      const encodedPath = encodeURIComponent(destinationPath);
      const response = await this.client.request({
        method: 'POST',
        url: `/api/file/upload/${encodedPath}`,
        data: formData,
        timeout: 60000,
      });

      const resultData = response.data;

      return {
        success: resultData.success ?? true,
        source_path: finalFileName,
        destination_path: destinationPath,
        file_size: resultData.file_size,
        error: resultData.error,
      };
    } catch (error) {
      console.error(`Remote file upload failed: ${error}`);
      return {
        success: false,
        source_path: fileName || 'unknown',
        destination_path: destinationPath,
        error: error instanceof Error ? error.message : String(error),
      };
    }
  }

  async fileDownload(sourcePath: string): Promise<FileDownloadResult> {
    console.debug(`Remote file download: ${sourcePath}`);

    try {
      const response = await this.client.get(
        `/api/file/download/${encodeURIComponent(sourcePath)}`,
        {
          timeout: 60000,
        }
      );

      // Convert response data to appropriate format
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
        // For other data types, stringify and create blob
        const stringData = JSON.stringify(response.data);
        content = stringData;
        fileSize = new Blob([stringData]).size;
      }

      return {
        success: true,
        source_path: sourcePath,
        content: content,
        file_size: fileSize,
      };
    } catch (error) {
      console.error(`Remote file download failed: ${error}`);
      return {
        success: false,
        source_path: sourcePath,
        content: '',
        error: error instanceof Error ? error.message : String(error),
      };
    }
  }

  async gitChanges(path: string): Promise<GitChange[]> {
    try {
      const response = await this.client.get('/api/git/changes', {
        params: { path },
      });
      return response.data;
    } catch (error) {
      throw new Error(
        `Failed to get git changes: ${error instanceof Error ? error.message : String(error)}`
      );
    }
  }

  async gitDiff(path: string): Promise<GitDiff> {
    try {
      const response = await this.client.get('/api/git/diff', {
        params: { path },
      });
      return response.data;
    } catch (error) {
      throw new Error(
        `Failed to get git diff: ${error instanceof Error ? error.message : String(error)}`
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

    // Create a temporary URL for the blob
    const url = URL.createObjectURL(blob);

    // Create a temporary anchor element to trigger download
    const a = document.createElement('a');
    a.href = url;
    a.download = saveAsFileName || sourcePath.split('/').pop() || 'download';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);

    // Clean up the temporary URL
    URL.revokeObjectURL(url);
  }

  close(): void {
    this.client.close();
  }
}
