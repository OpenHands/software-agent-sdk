import { runtimeServiceConnections } from './runtime-transport';
import type { RuntimeServiceOptions } from './runtime-transport';
import type { HttpClient } from './http-client';
import type { GitChange, GitDiff } from '../models/workspace';
import type { GitQueryOptions } from '../workspace/base';

export class GitClient {
  private readonly client: HttpClient;

  constructor(options: RuntimeServiceOptions) {
    this.client = runtimeServiceConnections(options).runtime;
  }

  async changes(path: string, options: GitQueryOptions = {}): Promise<GitChange[]> {
    return (
      await this.client.get<GitChange[]>('/api/git/changes', {
        params: { path, ref: options.ref },
      })
    ).data;
  }

  async diff(path: string, options: GitQueryOptions = {}): Promise<GitDiff> {
    return (
      await this.client.get<GitDiff>('/api/git/diff', {
        params: { path, ref: options.ref },
      })
    ).data;
  }
}
