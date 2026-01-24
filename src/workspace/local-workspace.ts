/**
 * Local workspace stub for browser compatibility
 *
 * This is a stub implementation of the IWorkspace interface for local execution.
 * The actual implementation requires Node.js APIs (child_process, fs) which are
 * not available in browser environments.
 *
 * For Node.js environments, a full implementation can be provided separately.
 * This stub allows the SDK to be imported in browser environments without errors.
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
 * Error thrown when LocalWorkspace methods are called in browser environments.
 */
class LocalWorkspaceNotSupportedError extends Error {
  constructor(method: string) {
    super(
      `LocalWorkspace.${method}() is not supported in browser environments. ` +
        `LocalWorkspace requires Node.js APIs (child_process, fs). ` +
        `Use RemoteWorkspace for browser-based applications.`
    );
    this.name = 'LocalWorkspaceNotSupportedError';
  }
}

/**
 * Local workspace stub for browser compatibility.
 *
 * This is a placeholder implementation that throws descriptive errors when methods
 * are called. It allows the SDK to be imported in browser environments without
 * causing module resolution errors for Node.js-specific modules.
 *
 * For actual local workspace functionality, use this class in a Node.js environment
 * with a proper implementation, or use RemoteWorkspace for browser applications.
 *
 * Example (browser - will throw errors):
 * ```typescript
 * const workspace = new LocalWorkspace({ workingDir: '/path/to/project' });
 * // This will throw LocalWorkspaceNotSupportedError
 * await workspace.executeCommand('ls -la');
 * ```
 *
 * For browser applications, use RemoteWorkspace instead:
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
   * @throws LocalWorkspaceNotSupportedError - Always throws in browser environments
   */
  async executeCommand(_command: string, _cwd?: string, _timeout?: number): Promise<CommandResult> {
    throw new LocalWorkspaceNotSupportedError('executeCommand');
  }

  /**
   * Write content to a file in the workspace.
   *
   * @throws LocalWorkspaceNotSupportedError - Always throws in browser environments
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
   * @throws LocalWorkspaceNotSupportedError - Always throws in browser environments
   */
  async fileDownload(_sourcePath: string): Promise<FileDownloadResult> {
    throw new LocalWorkspaceNotSupportedError('fileDownload');
  }

  /**
   * Get git changes for a repository.
   *
   * @throws LocalWorkspaceNotSupportedError - Always throws in browser environments
   */
  async gitChanges(_repoPath: string): Promise<GitChange[]> {
    throw new LocalWorkspaceNotSupportedError('gitChanges');
  }

  /**
   * Get git diff for a repository.
   *
   * @throws LocalWorkspaceNotSupportedError - Always throws in browser environments
   */
  async gitDiff(_repoPath: string): Promise<GitDiff> {
    throw new LocalWorkspaceNotSupportedError('gitDiff');
  }

  /**
   * Convenience method to write text content as a file.
   *
   * @throws LocalWorkspaceNotSupportedError - Always throws in browser environments
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
   * @throws LocalWorkspaceNotSupportedError - Always throws in browser environments
   */
  async uploadFileObject(_file: File, _destinationPath: string): Promise<FileOperationResult> {
    throw new LocalWorkspaceNotSupportedError('uploadFileObject');
  }

  /**
   * Convenience method to download file content as text.
   *
   * @throws LocalWorkspaceNotSupportedError - Always throws in browser environments
   */
  async downloadAsText(_sourcePath: string): Promise<string> {
    throw new LocalWorkspaceNotSupportedError('downloadAsText');
  }

  /**
   * Convenience method to download file content as a Blob.
   *
   * @throws LocalWorkspaceNotSupportedError - Always throws in browser environments
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
