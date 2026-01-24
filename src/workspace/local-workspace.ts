/**
 * Local workspace stub for executing commands and file operations
 *
 * This is a stub implementation of the IWorkspace interface for local execution.
 * The actual implementation requires Node.js APIs (child_process, fs) which are
 * not available in browser environments.
 *
 * This stub allows the library to be imported in browser environments without
 * causing build errors. All methods throw an error indicating that LocalWorkspace
 * is not available in the browser.
 *
 * For browser environments, use RemoteWorkspace instead.
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

const NOT_IMPLEMENTED_ERROR = 'LocalWorkspace is not available in browser environments. Use RemoteWorkspace instead, or use a Node.js-specific build.';

/**
 * Local workspace stub that throws errors for all operations.
 *
 * This is a placeholder for the LocalWorkspace implementation. The actual
 * implementation requires Node.js APIs (child_process, fs) which are not
 * available in browser environments.
 *
 * For browser environments, use RemoteWorkspace to connect to an OpenHands
 * agent server.
 *
 * Example (browser - use RemoteWorkspace):
 * ```typescript
 * import { RemoteWorkspace } from '@openhands/typescript-client';
 *
 * const workspace = new RemoteWorkspace({
 *   host: 'http://localhost:8000',
 *   workingDir: '/workspace'
 * });
 * ```
 */
export class LocalWorkspace implements IWorkspace {
  public readonly workingDir: string;

  constructor(options: LocalWorkspaceOptions) {
    this.workingDir = options.workingDir;
  }

  async executeCommand(
    _command: string,
    _cwd?: string,
    _timeout?: number
  ): Promise<CommandResult> {
    throw new Error(NOT_IMPLEMENTED_ERROR);
  }

  async fileUpload(
    _content: string | Blob | File,
    _destinationPath: string,
    _fileName?: string
  ): Promise<FileOperationResult> {
    throw new Error(NOT_IMPLEMENTED_ERROR);
  }

  async fileDownload(_sourcePath: string): Promise<FileDownloadResult> {
    throw new Error(NOT_IMPLEMENTED_ERROR);
  }

  async gitChanges(_repoPath: string): Promise<GitChange[]> {
    throw new Error(NOT_IMPLEMENTED_ERROR);
  }

  async gitDiff(_repoPath: string): Promise<GitDiff> {
    throw new Error(NOT_IMPLEMENTED_ERROR);
  }

  async uploadText(
    _text: string,
    _destinationPath: string,
    _fileName?: string
  ): Promise<FileOperationResult> {
    throw new Error(NOT_IMPLEMENTED_ERROR);
  }

  async uploadFileObject(_file: File, _destinationPath: string): Promise<FileOperationResult> {
    throw new Error(NOT_IMPLEMENTED_ERROR);
  }

  async downloadAsText(_sourcePath: string): Promise<string> {
    throw new Error(NOT_IMPLEMENTED_ERROR);
  }

  async downloadAsBlob(_sourcePath: string): Promise<Blob> {
    throw new Error(NOT_IMPLEMENTED_ERROR);
  }

  close(): void {
    // No-op for stub
  }
}
