/**
 * Local workspace stub implementation.
 *
 * This is a stub implementation of the IWorkspace interface for local execution.
 * All methods throw descriptive errors directing users to RemoteWorkspace.
 *
 * This mirrors the Python SDK's LocalWorkspace class architecture.
 */

import {
  CommandResult,
  FileOperationResult,
  FileDownloadResult,
  GitChange,
  GitDiff,
} from '../models/workspace';
import { IWorkspace, BaseWorkspaceOptions } from './base';

/**
 * Options for creating a LocalWorkspace instance.
 */
export type LocalWorkspaceOptions = BaseWorkspaceOptions;

/**
 * Error thrown when LocalWorkspace methods are called.
 */
class LocalWorkspaceNotSupportedError extends Error {
  constructor(method: string) {
    super(`LocalWorkspace.${method}() is not implemented. ` + `Use RemoteWorkspace instead.`);
    this.name = 'LocalWorkspaceNotSupportedError';
  }
}

/**
 * Local workspace stub.
 *
 * This is a placeholder implementation that throws descriptive errors when methods
 * are called. Use RemoteWorkspace for actual workspace functionality.
 *
 * ```typescript
 * const workspace = new RemoteWorkspace({
 *   host: 'http://localhost:8000',
 *   workingDir: '/workspace'
 * });
 * const result = await workspace.executeCommand('ls -la');
 * ```
 */
export class LocalWorkspace implements IWorkspace {
  public readonly workingDir: string;

  constructor(options: LocalWorkspaceOptions) {
    this.workingDir = options.workingDir;
  }

  /**
   * Execute a bash command locally.
   *
   * @throws LocalWorkspaceNotSupportedError - Always throws — not implemented
   */
  async executeCommand(_command: string, _cwd?: string, _timeout?: number): Promise<CommandResult> {
    throw new LocalWorkspaceNotSupportedError('executeCommand');
  }

  /**
   * Write content to a file in the workspace.
   *
   * @throws LocalWorkspaceNotSupportedError - Always throws — not implemented
   */
  async fileUpload(
    _content: string | Blob | File,
    _destinationPath: string,
    _fileName?: string
  ): Promise<FileOperationResult> {
    throw new LocalWorkspaceNotSupportedError('fileUpload');
  }

  /**
   * Read a file from the workspace.
   *
   * @throws LocalWorkspaceNotSupportedError - Always throws — not implemented
   */
  async fileDownload(_sourcePath: string): Promise<FileDownloadResult> {
    throw new LocalWorkspaceNotSupportedError('fileDownload');
  }

  /**
   * Get git changes for a repository.
   *
   * @throws LocalWorkspaceNotSupportedError - Always throws — not implemented
   */
  async gitChanges(_repoPath: string): Promise<GitChange[]> {
    throw new LocalWorkspaceNotSupportedError('gitChanges');
  }

  /**
   * Get git diff for a repository.
   *
   * @throws LocalWorkspaceNotSupportedError - Always throws — not implemented
   */
  async gitDiff(_repoPath: string): Promise<GitDiff> {
    throw new LocalWorkspaceNotSupportedError('gitDiff');
  }

  /**
   * Convenience method to write text content as a file.
   *
   * @throws LocalWorkspaceNotSupportedError - Always throws — not implemented
   */
  async uploadText(
    _text: string,
    _destinationPath: string,
    _fileName?: string
  ): Promise<FileOperationResult> {
    throw new LocalWorkspaceNotSupportedError('uploadText');
  }

  /**
   * Convenience method to upload a File object.
   *
   * @throws LocalWorkspaceNotSupportedError - Always throws — not implemented
   */
  async uploadFileObject(_file: File, _destinationPath: string): Promise<FileOperationResult> {
    throw new LocalWorkspaceNotSupportedError('uploadFileObject');
  }

  /**
   * Convenience method to download file content as text.
   *
   * @throws LocalWorkspaceNotSupportedError - Always throws — not implemented
   */
  async downloadAsText(_sourcePath: string): Promise<string> {
    throw new LocalWorkspaceNotSupportedError('downloadAsText');
  }

  /**
   * Convenience method to download file content as a Blob.
   *
   * @throws LocalWorkspaceNotSupportedError - Always throws — not implemented
   */
  async downloadAsBlob(_sourcePath: string): Promise<Blob> {
    throw new LocalWorkspaceNotSupportedError('downloadAsBlob');
  }

  /**
   * Close/cleanup the workspace.
   *
   * For the stub implementation, this is a no-op.
   */
  close(): void {
    // No-op for stub implementation
  }
}
