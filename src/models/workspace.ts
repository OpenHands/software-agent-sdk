/**
 * Workspace operation result models
 */

export interface CommandResult {
  command: string;
  exit_code: number;
  stdout: string;
  stderr: string;
  timeout_occurred: boolean;
}

export interface FileOperationResult {
  success: boolean;
  source_path: string;
  destination_path: string;
  file_size?: number;
  error?: string;
}

export interface GitChange {
  path: string;
  status: 'added' | 'modified' | 'deleted' | 'renamed';
  [key: string]: any;
}

export interface GitDiff {
  path: string;
  diff: string;
  [key: string]: any;
}
