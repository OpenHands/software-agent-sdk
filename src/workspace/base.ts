/**
 * Base workspace interface and types
 *
 * This file defines the abstract interface that all workspace implementations must follow.
 * It mirrors the Python SDK's BaseWorkspace pattern.
 */

import {
  CommandResult,
  FileOperationResult,
  FileDownloadResult,
  GitChange,
  GitDiff,
} from '../models/workspace';

/**
 * Base workspace options that all workspace types share
 */
export interface BaseWorkspaceOptions {
  workingDir: string;
}

/**
 * Abstract interface for workspace implementations.
 *
 * Workspaces provide a sandboxed environment where agents can execute commands,
 * read/write files, and perform other operations. All workspace implementations
 * support the same interface for interoperability.
 *
 * This mirrors the Python SDK's BaseWorkspace abstract class.
 */
export interface IWorkspace {
  /** The working directory for agent operations */
  readonly workingDir: string;

  /**
   * Execute a bash command in the workspace.
   *
   * @param command - The bash command to execute
   * @param cwd - Working directory for the command (optional)
   * @param timeout - Timeout in seconds (defaults to 30.0)
   * @returns CommandResult containing stdout, stderr, exit_code, and other metadata
   */
  executeCommand(command: string, cwd?: string, timeout?: number): Promise<CommandResult>;

  /**
   * Upload content to the workspace.
   *
   * @param content - The content to upload (string, Blob, or File)
   * @param destinationPath - Path where the content should be uploaded
   * @param fileName - Optional filename for the uploaded content
   * @returns FileOperationResult containing success status and metadata
   */
  fileUpload(
    content: string | Blob | File,
    destinationPath: string,
    fileName?: string
  ): Promise<FileOperationResult>;

  /**
   * Download a file from the workspace.
   *
   * @param sourcePath - Path to the source file in the workspace
   * @returns FileDownloadResult containing the file content
   */
  fileDownload(sourcePath: string): Promise<FileDownloadResult>;

  /**
   * Get git changes for a repository at the given path.
   *
   * @param path - Path to the git repository
   * @returns Array of GitChange objects
   */
  gitChanges(path: string): Promise<GitChange[]>;

  /**
   * Get git diff for a repository at the given path.
   *
   * @param path - Path to the git repository
   * @returns GitDiff object containing the diff content
   */
  gitDiff(path: string): Promise<GitDiff>;

  /**
   * Convenience method to upload text content as a file.
   *
   * @param text - The text content to upload
   * @param destinationPath - Path where the content should be uploaded
   * @param fileName - Optional filename
   */
  uploadText(
    text: string,
    destinationPath: string,
    fileName?: string
  ): Promise<FileOperationResult>;

  /**
   * Convenience method to upload a File object.
   *
   * @param file - The File object to upload
   * @param destinationPath - Path where the file should be uploaded
   */
  uploadFileObject(file: File, destinationPath: string): Promise<FileOperationResult>;

  /**
   * Convenience method to download file content as text.
   *
   * @param sourcePath - Path to the source file
   * @returns The file content as a string
   */
  downloadAsText(sourcePath: string): Promise<string>;

  /**
   * Convenience method to download file content as a Blob.
   *
   * @param sourcePath - Path to the source file
   * @returns The file content as a Blob
   */
  downloadAsBlob(sourcePath: string): Promise<Blob>;

  /**
   * Close/cleanup the workspace connection.
   * For local workspaces, this is typically a no-op.
   * For remote workspaces, this closes the HTTP client connection.
   */
  close(): void;
}

/**
 * Type discriminator for workspace types.
 * Useful for runtime type checking and factory patterns.
 */
export type WorkspaceType = 'local' | 'remote';
