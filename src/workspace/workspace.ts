/**
 * Workspace factory function that returns RemoteWorkspace
 * Matches the Python SDK pattern: Workspace(host="http://...")
 */

import { RemoteWorkspace, RemoteWorkspaceOptions } from './remote-workspace';

/**
 * Workspace class that extends RemoteWorkspace.
 * Provides a cleaner API that matches the Python SDK naming.
 * 
 * Usage:
 *   const workspace = new Workspace({ host: 'http://localhost:8000', apiKey: 'key' });
 */
export class Workspace extends RemoteWorkspace {
  constructor(options: RemoteWorkspaceOptions) {
    super(options);
  }
}