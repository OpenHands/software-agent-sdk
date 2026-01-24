/**
 * Local workspace implementation for executing commands and file operations
 *
 * This implements the IWorkspace interface for local execution. Unlike RemoteWorkspace,
 * LocalWorkspace operates directly on the local filesystem and executes commands locally.
 *
 * This mirrors the Python SDK's LocalWorkspace class.
 *
 * NOTE: This implementation uses Node.js APIs and will not work in browser environments.
 */

import { spawn } from 'child_process';
import * as fs from 'fs/promises';
import * as path from 'path';
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
 * Local workspace implementation that operates on the host filesystem.
 *
 * LocalWorkspace provides direct access to the local filesystem and command execution
 * environment. It's suitable for development and testing scenarios where the agent
 * should operate directly on the host system.
 *
 * NOTE: This implementation uses Node.js APIs (child_process, fs) and will not work
 * in browser environments.
 *
 * Example:
 * ```typescript
 * const workspace = new LocalWorkspace({
 *   workingDir: '/path/to/project'
 * });
 * const result = await workspace.executeCommand('ls -la');
 * console.log(result.stdout);
 * workspace.close();
 * ```
 */
export class LocalWorkspace implements IWorkspace {
  public readonly workingDir: string;

  constructor(options: LocalWorkspaceOptions) {
    this.workingDir = options.workingDir;
  }

  /**
   * Execute a bash command locally using child_process.
   *
   * @param command - The bash command to execute
   * @param cwd - Working directory for the command (defaults to workingDir)
   * @param timeout - Timeout in seconds (defaults to 30)
   * @returns CommandResult with stdout, stderr, and exit code
   */
  async executeCommand(
    command: string,
    cwd?: string,
    timeout: number = 30.0
  ): Promise<CommandResult> {
    const workingDirectory = cwd || this.workingDir;
    const timeoutMs = timeout * 1000;

    return new Promise((resolve) => {
      const stdoutChunks: Buffer[] = [];
      const stderrChunks: Buffer[] = [];
      let timeoutOccurred = false;
      let exitCode: number | null = null;

      // Use bash -c to execute the command string
      const proc = spawn('bash', ['-c', command], {
        cwd: workingDirectory,
        env: process.env,
        stdio: ['pipe', 'pipe', 'pipe'],
      });

      // Set up timeout
      const timeoutId = setTimeout(() => {
        timeoutOccurred = true;
        proc.kill('SIGTERM');
        // Force kill after 5 seconds if SIGTERM doesn't work
        setTimeout(() => {
          if (!proc.killed) {
            proc.kill('SIGKILL');
          }
        }, 5000);
      }, timeoutMs);

      proc.stdout.on('data', (data: Buffer) => {
        stdoutChunks.push(data);
      });

      proc.stderr.on('data', (data: Buffer) => {
        stderrChunks.push(data);
      });

      proc.on('close', (code) => {
        clearTimeout(timeoutId);
        exitCode = code;

        const stdout = Buffer.concat(stdoutChunks).toString('utf-8');
        const stderr = Buffer.concat(stderrChunks).toString('utf-8');

        resolve({
          command,
          exit_code: timeoutOccurred ? -1 : (exitCode ?? -1),
          stdout,
          stderr: timeoutOccurred ? `${stderr}\nCommand timed out after ${timeout} seconds` : stderr,
          timeout_occurred: timeoutOccurred,
        });
      });

      proc.on('error', (err) => {
        clearTimeout(timeoutId);
        resolve({
          command,
          exit_code: -1,
          stdout: '',
          stderr: `Failed to execute command: ${err.message}`,
          timeout_occurred: false,
        });
      });
    });
  }

  /**
   * Write content to a file in the workspace.
   *
   * @param content - The content to write (string, Blob, or File)
   * @param destinationPath - Path where the content should be written
   * @param fileName - Optional filename (used to construct full path if destinationPath is a directory)
   * @returns FileOperationResult with success status
   */
  async fileUpload(
    content: string | Blob | File,
    destinationPath: string,
    fileName?: string
  ): Promise<FileOperationResult> {
    try {
      // Resolve the full path
      let fullPath = path.isAbsolute(destinationPath)
        ? destinationPath
        : path.join(this.workingDir, destinationPath);

      // If fileName is provided, append it to the path
      if (fileName) {
        fullPath = path.join(fullPath, fileName);
      }

      // Ensure the directory exists
      const dir = path.dirname(fullPath);
      await fs.mkdir(dir, { recursive: true });

      // Convert content to Buffer
      let buffer: Buffer;
      if (typeof content === 'string') {
        buffer = Buffer.from(content, 'utf-8');
      } else {
        // Blob or File - both have arrayBuffer() method
        const arrayBuffer = await content.arrayBuffer();
        buffer = Buffer.from(arrayBuffer);
      }

      // Write the file
      await fs.writeFile(fullPath, buffer);

      // Get file stats for size
      const stats = await fs.stat(fullPath);

      return {
        success: true,
        source_path: fileName || 'content',
        destination_path: fullPath,
        file_size: stats.size,
      };
    } catch (error) {
      return {
        success: false,
        source_path: fileName || 'content',
        destination_path: destinationPath,
        error: error instanceof Error ? error.message : String(error),
      };
    }
  }

  /**
   * Read a file from the workspace.
   *
   * @param sourcePath - Path to the file to read
   * @returns FileDownloadResult with file content
   */
  async fileDownload(sourcePath: string): Promise<FileDownloadResult> {
    try {
      // Resolve the full path
      const fullPath = path.isAbsolute(sourcePath)
        ? sourcePath
        : path.join(this.workingDir, sourcePath);

      // Read the file
      const content = await fs.readFile(fullPath, 'utf-8');
      const stats = await fs.stat(fullPath);

      return {
        success: true,
        source_path: sourcePath,
        content,
        file_size: stats.size,
      };
    } catch (error) {
      return {
        success: false,
        source_path: sourcePath,
        content: '',
        error: error instanceof Error ? error.message : String(error),
      };
    }
  }

  /**
   * Get git changes for a repository.
   *
   * @param repoPath - Path to the git repository (relative to workingDir or absolute)
   * @returns Array of GitChange objects
   */
  async gitChanges(repoPath: string): Promise<GitChange[]> {
    const fullPath = path.isAbsolute(repoPath)
      ? repoPath
      : path.join(this.workingDir, repoPath);

    // Run git status with porcelain format for easy parsing
    const result = await this.executeCommand(
      'git status --porcelain',
      fullPath
    );

    if (result.exit_code !== 0) {
      throw new Error(`Failed to get git changes: ${result.stderr}`);
    }

    const changes: GitChange[] = [];
    const lines = result.stdout.split('\n').filter(line => line.trim());

    for (const line of lines) {
      const statusCode = line.substring(0, 2);
      const filePath = line.substring(3).trim();

      // Map git status codes to our status types
      let status: 'added' | 'modified' | 'deleted' | 'renamed';
      if (statusCode.includes('A') || statusCode.includes('?')) {
        status = 'added';
      } else if (statusCode.includes('D')) {
        status = 'deleted';
      } else if (statusCode.includes('R')) {
        status = 'renamed';
      } else {
        status = 'modified';
      }

      changes.push({
        path: filePath,
        status,
        git_status_code: statusCode,
      });
    }

    return changes;
  }

  /**
   * Get git diff for a repository.
   *
   * @param repoPath - Path to the git repository (relative to workingDir or absolute)
   * @returns GitDiff object with the diff content
   */
  async gitDiff(repoPath: string): Promise<GitDiff> {
    const fullPath = path.isAbsolute(repoPath)
      ? repoPath
      : path.join(this.workingDir, repoPath);

    // Get both staged and unstaged changes
    const stagedResult = await this.executeCommand(
      'git diff --cached',
      fullPath
    );

    const unstagedResult = await this.executeCommand(
      'git diff',
      fullPath
    );

    if (stagedResult.exit_code !== 0 && unstagedResult.exit_code !== 0) {
      throw new Error(`Failed to get git diff: ${stagedResult.stderr || unstagedResult.stderr}`);
    }

    const diffParts: string[] = [];
    if (stagedResult.stdout.trim()) {
      diffParts.push('=== Staged Changes ===\n' + stagedResult.stdout);
    }
    if (unstagedResult.stdout.trim()) {
      diffParts.push('=== Unstaged Changes ===\n' + unstagedResult.stdout);
    }

    return {
      path: repoPath,
      diff: diffParts.join('\n\n') || 'No changes',
    };
  }

  /**
   * Convenience method to write text content as a file.
   */
  async uploadText(
    text: string,
    destinationPath: string,
    fileName?: string
  ): Promise<FileOperationResult> {
    return this.fileUpload(text, destinationPath, fileName);
  }

  /**
   * Convenience method to upload a File object.
   */
  async uploadFileObject(file: File, destinationPath: string): Promise<FileOperationResult> {
    return this.fileUpload(file, destinationPath, file.name);
  }

  /**
   * Convenience method to download file content as text.
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
   * Convenience method to download file content as a Blob.
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
   * Close/cleanup the workspace.
   *
   * For local workspaces, this is typically a no-op since there are no
   * connections to close.
   */
  close(): void {
    // No-op for local workspace - nothing to clean up
  }
}
