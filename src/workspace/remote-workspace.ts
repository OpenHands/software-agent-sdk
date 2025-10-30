/**
 * Remote workspace implementation for executing commands and file operations
 */

import { HttpClient } from '../client/http-client.js';
import { CommandResult, FileOperationResult, GitChange, GitDiff } from '../models/workspace.js';

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
      // Step 1: Start the bash command
      const payload: any = {
        command,
        timeout: Math.floor(timeout),
      };
      
      if (cwd) {
        payload.cwd = cwd;
      }

      const startResponse = await this.client.post(
        '/api/bash/start_bash_command',
        payload,
        { timeout: (timeout + 5) * 1000 }
      );
      
      const bashCommand = startResponse.data;
      const commandId = bashCommand.id;
      
      console.debug(`Started command with ID: ${commandId}`);

      // Step 2: Poll for output until command completes
      const startTime = Date.now();
      const stdoutParts: string[] = [];
      const stderrParts: string[] = [];
      let exitCode: number | null = null;

      while ((Date.now() - startTime) / 1000 < timeout) {
        // Search for all events
        const searchResponse = await this.client.get('/api/bash/bash_events/search', {
          params: {
            command_id__eq: commandId,
            sort_order: 'TIMESTAMP',
            limit: 100,
          },
          timeout: timeout * 1000,
        });
        
        const searchResult = searchResponse.data;

        // Filter for BashOutput events for this command
        for (const event of searchResult.items || []) {
          if (event.kind === 'BashOutput') {
            if (event.stdout) {
              stdoutParts.push(event.stdout);
            }
            if (event.stderr) {
              stderrParts.push(event.stderr);
            }
            if (event.exit_code !== undefined && event.exit_code !== null) {
              exitCode = event.exit_code;
            }
          }
        }

        // If we have an exit code, the command is complete
        if (exitCode !== null) {
          break;
        }

        // Wait a bit before polling again
        await new Promise(resolve => setTimeout(resolve, 100));
      }

      // If we timed out waiting for completion
      if (exitCode === null) {
        console.warn(`Command timed out after ${timeout} seconds: ${command}`);
        exitCode = -1;
        stderrParts.push(`Command timed out after ${timeout} seconds`);
      }

      // Combine all output parts
      const stdout = stdoutParts.join('');
      const stderr = stderrParts.join('');

      return {
        command,
        exit_code: exitCode,
        stdout,
        stderr,
        timeout_occurred: exitCode === -1 && stderr.includes('timed out'),
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

  async fileUpload(sourcePath: string, destinationPath: string): Promise<FileOperationResult> {
    console.debug(`Remote file upload: ${sourcePath} -> ${destinationPath}`);

    try {
      // For browser environments, this would need to be adapted to work with File objects
      // For Node.js environments, we can read the file
      const fs = await import('fs');
      const path = await import('path');
      
      const fileContent = await fs.promises.readFile(sourcePath);
      const fileName = path.basename(sourcePath);
      
      // Create FormData for file upload
      const formData = new FormData();
      const blob = new Blob([fileContent]);
      formData.append('file', blob, fileName);
      formData.append('destination_path', destinationPath);

      const response = await this.client.request({
        method: 'POST',
        url: '/api/file/upload',
        data: formData,
        timeout: 60000,
      });

      const resultData = response.data;

      return {
        success: resultData.success ?? true,
        source_path: sourcePath,
        destination_path: destinationPath,
        file_size: resultData.file_size,
        error: resultData.error,
      };

    } catch (error) {
      console.error(`Remote file upload failed: ${error}`);
      return {
        success: false,
        source_path: sourcePath,
        destination_path: destinationPath,
        error: error instanceof Error ? error.message : String(error),
      };
    }
  }

  async fileDownload(sourcePath: string, destinationPath: string): Promise<FileOperationResult> {
    console.debug(`Remote file download: ${sourcePath} -> ${destinationPath}`);

    try {
      const response = await this.client.get(`/api/file/download/${encodeURIComponent(sourcePath)}`, {
        timeout: 60000,
      });

      // For Node.js environments, write the file
      const fs = await import('fs');
      const path = await import('path');
      
      // Ensure destination directory exists
      const destDir = path.dirname(destinationPath);
      await fs.promises.mkdir(destDir, { recursive: true });
      
      // Write the file content
      const content = typeof response.data === 'string' ? response.data : JSON.stringify(response.data);
      await fs.promises.writeFile(destinationPath, content);
      
      const stats = await fs.promises.stat(destinationPath);

      return {
        success: true,
        source_path: sourcePath,
        destination_path: destinationPath,
        file_size: stats.size,
      };

    } catch (error) {
      console.error(`Remote file download failed: ${error}`);
      return {
        success: false,
        source_path: sourcePath,
        destination_path: destinationPath,
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
      throw new Error(`Failed to get git changes: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async gitDiff(path: string): Promise<GitDiff> {
    try {
      const response = await this.client.get('/api/git/diff', {
        params: { path },
      });
      return response.data;
    } catch (error) {
      throw new Error(`Failed to get git diff: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  close(): void {
    this.client.close();
  }
}