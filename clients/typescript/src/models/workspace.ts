/**
 * Workspace and bash operation result models
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

export interface FileDownloadResult {
  success: boolean;
  source_path: string;
  content: string | Blob;
  file_size?: number;
  error?: string;
}

export interface GitChange {
  path: string;
  status: 'added' | 'modified' | 'deleted' | 'renamed';
  [key: string]: unknown;
}

export interface GitDiff {
  path?: string;
  diff?: string;
  original?: string | null;
  modified?: string | null;
  [key: string]: unknown;
}

export interface ExecuteBashRequest {
  command: string;
  cwd?: string;
  timeout?: number;
}

export interface BashEventBase {
  id: string;
  timestamp: string;
  kind?: string;
  [key: string]: unknown;
}

export interface BashCommand extends BashEventBase, ExecuteBashRequest {
  kind?: 'BashCommand';
}

export interface BashOutput extends BashEventBase {
  kind?: 'BashOutput';
  command_id: string;
  order: number;
  exit_code?: number | null;
  stdout?: string | null;
  stderr?: string | null;
}

export interface BashError extends BashEventBase {
  kind?: 'BashError';
  code: string;
  detail: string;
}

export type BashEvent = BashCommand | BashOutput | BashError;

export interface BashEventPage {
  items: BashEvent[];
  next_page_id?: string;
}

export interface BashEventSearchOptions {
  kind__eq?: 'BashCommand' | 'BashOutput';
  command_id__eq?: string;
  timestamp__gte?: string;
  timestamp__lt?: string;
  order__gt?: number;
  sort_order?: 'TIMESTAMP' | 'TIMESTAMP_DESC';
  page_id?: string;
  limit?: number;
}

export interface ClearBashEventsResponse {
  cleared_count: number;
}
