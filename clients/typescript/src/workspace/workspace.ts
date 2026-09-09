/**
 * Workspace factory and utility functions
 *
 * This module provides a convenient factory pattern for creating workspace instances.
 * It matches the Python SDK pattern while supporting both local and remote workspaces.
 */

import { IWorkspace, WorkspaceType } from './base';
import { RemoteWorkspace, RemoteWorkspaceOptions } from './remote-workspace';
import { LocalWorkspace, LocalWorkspaceOptions } from './local-workspace';

/**
 * Union type for all workspace options
 */
export type WorkspaceOptions = RemoteWorkspaceOptions | LocalWorkspaceOptions;

/**
 * Options for creating a workspace with explicit type selection
 */
export interface CreateWorkspaceOptions {
  type: WorkspaceType;
  options: WorkspaceOptions;
}

/**
 * Type guard to check if options are for RemoteWorkspace
 */
function isRemoteWorkspaceOptions(options: WorkspaceOptions): options is RemoteWorkspaceOptions {
  return 'host' in options;
}

/**
 * Workspace class that extends RemoteWorkspace for backwards compatibility.
 * Provides a cleaner API that matches the Python SDK naming.
 *
 * For new code, consider using the createWorkspace factory function
 * or directly instantiating RemoteWorkspace or LocalWorkspace.
 *
 * Usage:
 * ```typescript
 * // Legacy usage (backwards compatible)
 * const workspace = new Workspace({
 *   host: 'http://localhost:8000',
 *   workingDir: '/workspace',
 *   apiKey: 'key'
 * });
 * ```
 */
export class Workspace extends RemoteWorkspace {
  constructor(options: RemoteWorkspaceOptions) {
    super(options);
  }
}

/**
 * Factory function to create a workspace instance based on options.
 *
 * This provides a unified way to create either local or remote workspaces
 * based on the provided options.
 *
 * Usage:
 * ```typescript
 * // Create a remote workspace
 * const remoteWorkspace = createWorkspace({
 *   type: 'remote',
 *   options: {
 *     host: 'http://localhost:8000',
 *     workingDir: '/workspace',
 *     apiKey: 'key'
 *   }
 * });
 *
 * // Create a local workspace
 * const localWorkspace = createWorkspace({
 *   type: 'local',
 *   options: {
 *     workingDir: '/path/to/project'
 *   }
 * });
 * ```
 *
 * @param config - The workspace configuration including type and options
 * @returns A workspace instance implementing IWorkspace
 */
export function createWorkspace(config: CreateWorkspaceOptions): IWorkspace {
  switch (config.type) {
    case 'remote':
      if (!isRemoteWorkspaceOptions(config.options)) {
        throw new Error('RemoteWorkspace requires host option');
      }
      return new RemoteWorkspace(config.options);

    case 'local':
      return new LocalWorkspace(config.options as LocalWorkspaceOptions);

    default:
      throw new Error(`Unknown workspace type: ${config.type}`);
  }
}

/**
 * Create a workspace automatically detecting the type from options.
 *
 * If the options contain a 'host' property, creates a RemoteWorkspace.
 * Otherwise, creates a LocalWorkspace.
 *
 * Usage:
 * ```typescript
 * // Automatically creates RemoteWorkspace (has 'host')
 * const remote = createWorkspaceAuto({
 *   host: 'http://localhost:8000',
 *   workingDir: '/workspace'
 * });
 *
 * // Automatically creates LocalWorkspace (no 'host')
 * const local = createWorkspaceAuto({
 *   workingDir: '/path/to/project'
 * });
 * ```
 *
 * @param options - The workspace options
 * @returns A workspace instance implementing IWorkspace
 */
export function createWorkspaceAuto(options: WorkspaceOptions): IWorkspace {
  if (isRemoteWorkspaceOptions(options)) {
    return new RemoteWorkspace(options);
  }
  return new LocalWorkspace(options as LocalWorkspaceOptions);
}
