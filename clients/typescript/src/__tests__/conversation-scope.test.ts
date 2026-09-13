import { createServer, Server } from 'node:http';
import { AddressInfo } from 'node:net';
import { ServerClient } from '../client/server-client';
import { ConversationManager } from '../conversation/conversation-manager';
import { FileClient } from '../client/file-client';
import { MCPClient } from '../client/mcp-client';
import { HttpClient } from '../client/http-client';
import { RuntimeClient } from '../client/runtime-client';
import type { AgentBase } from '../types/base';
import { RemoteWorkspace } from '../workspace/remote-workspace';

describe('conversation-scoped requests', () => {
  let server: Server;
  let host: string;
  const urls: string[] = [];
  let scoped = false;
  let discoveryStatus = 200;
  beforeAll(async () => {
    server = createServer((req, res) => {
      urls.push(req.url!);
      if (req.url === '/server_info') {
        res.statusCode = discoveryStatus;
        res.setHeader('content-type', 'application/json');
        res.end(
          JSON.stringify({
            version: '1.47.0',
            capabilities: scoped ? ['conversation_runtime_routes_v1'] : [],
          })
        );
        return;
      }
      if (
        req.url === '/api/conversations' ||
        req.url === '/api/conversations/created' ||
        req.url === '/api/conversations/created/fork'
      ) {
        res.setHeader('content-type', 'application/json');
        res.end(
          JSON.stringify({
            id: req.url.endsWith('/fork') ? 'forked' : 'created',
            agent: { kind: 'Agent' },
            workspace: { working_dir: '/workspace' },
          })
        );
        return;
      }
      res.setHeader('content-type', 'application/json');
      res.end(JSON.stringify({ exit_code: 0, stdout: 'ok', stderr: '' }));
    });
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    host = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });
  afterAll(async () => {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });
  it('advertises client-side runtime routing support', () => {
    expect(ServerClient.supportsConversationRuntimeRoutes).toBe(true);
  });
  it('scopes workspace commands to their conversation', async () => {
    const workspace = new RemoteWorkspace({
      host,
      workingDir: '/workspace',
      conversationId: 'demo-cid',
    });
    const result = await workspace.executeCommand('pwd');
    expect(result.stdout).toBe('ok');
    expect(urls.pop()).toBe('/api/bash/execute_bash_command?cid=demo-cid');
  });
  it('keeps runtime services scoped and setup operations global', async () => {
    scoped = true;
    try {
      const manager = new ConversationManager({ host });
      const runtime = manager.runtime('selected');
      await runtime.files.downloadFile('/workspace/a');
      expect(urls.pop()).toBe('/api/conversations/selected/file/download?path=%2Fworkspace%2Fa');
      await runtime.bash.executeCommand({ command: 'pwd' });
      expect(urls.pop()).toBe('/api/conversations/selected/bash/execute_bash_command');
      await manager.files.getHome();
      expect(urls.pop()).toBe('/api/file/home');
      await runtime.mcp.testServer({ server: { command: 'echo' } });
      expect(urls.pop()).toBe('/api/conversations/selected/mcp/test');
      await manager.mcp.getOAuthStatus('job');
      expect(urls.pop()).toBe('/api/mcp/oauth/status/job');
    } finally {
      scoped = false;
    }
  });
  it('preserves mixed-scope legacy constructors without classifying URLs', async () => {
    scoped = true;
    try {
      const files = new FileClient({ host, conversationId: 'legacy' });
      await files.getHome();
      expect(urls.pop()).toBe('/api/file/home');
      await files.downloadFile('/workspace/a');
      expect(urls.pop()).toContain('/api/conversations/legacy/file/download');
      const mcp = new MCPClient({ host, conversationId: 'legacy' });
      await mcp.getOAuthStatus('job');
      expect(urls.pop()).toBe('/api/mcp/oauth/status/job');
    } finally {
      scoped = false;
    }
  });
  it('rejects attempts to override immutable runtime identity', async () => {
    const runtime = new RuntimeClient({ host, conversationId: 'selected' });
    await expect(runtime.url('/api/file/download', { cid: 'other' })).rejects.toThrow(
      'cannot be overridden'
    );
    await expect(runtime.url('/api/file/../../settings')).rejects.toThrow('API path');
    await expect(runtime.files.downloadTrajectory('other')).rejects.toThrow('selected runtime');
    expect(() => new RuntimeClient({ host, conversationId: '' })).toThrow('conversation ID');
  });
  it('leaves generic HTTP requests untouched without discovery', async () => {
    const start = urls.length;
    await new HttpClient({ baseUrl: host }).get('/api/file/download', {
      params: { cid: 'explicit' },
    });
    expect(urls.slice(start)).toEqual(['/api/file/download?cid=explicit']);
  });
  it('coalesces capability discovery across concurrent runtime requests', async () => {
    scoped = true;
    const start = urls.length;
    const manager = new ConversationManager({ host });
    const client = manager.runtime('shared');
    const other = manager.runtime('other');
    try {
      await Promise.all([
        client.git.changes('/workspace'),
        other.files.downloadFile('/workspace/a'),
      ]);
      expect(urls.slice(start).filter((url) => url === '/server_info')).toHaveLength(1);
      expect(urls.slice(start)).toContain(
        '/api/conversations/shared/git/changes?path=%2Fworkspace'
      );
      expect(urls.slice(start)).toContain(
        '/api/conversations/other/file/download?path=%2Fworkspace%2Fa'
      );
    } finally {
      scoped = false;
    }
  });
  it('retries discovery after errors without silently downgrading', async () => {
    discoveryStatus = 503;
    const client = new RuntimeClient({ host, conversationId: 'retry' });
    const start = urls.length;
    try {
      await expect(client.files.downloadFile('/workspace/a')).rejects.toMatchObject({
        status: 503,
      });
      expect(urls.slice(start)).toEqual(['/server_info']);
      discoveryStatus = 200;
      scoped = true;
      await client.files.downloadFile('/workspace/a');
      expect(urls.pop()).toBe('/api/conversations/retry/file/download?path=%2Fworkspace%2Fa');
    } finally {
      discoveryStatus = 200;
      scoped = false;
    }
  });
  it('falls back when server info is unavailable on an older server', async () => {
    discoveryStatus = 404;
    try {
      const client = new RuntimeClient({ host, conversationId: 'legacy' });
      await client.files.downloadFile('/workspace/a');
      expect(urls.pop()).toBe('/api/file/download?path=%2Fworkspace%2Fa&cid=legacy');
    } finally {
      discoveryStatus = 200;
    }
  });
  it('shares the runtime instance with workspaces and keeps sessions global', async () => {
    scoped = true;
    try {
      const runtime = new ConversationManager({ host }).runtime('preview');
      const workspace = new RemoteWorkspace({ host, workingDir: '/workspace', runtime });
      expect(workspace.bash).toBe(runtime.bash);
      expect(workspace.client).toBe(runtime.transport);
      expect(await workspace.startWorkspaceSession('preview')).toBe(
        `${host}/api/conversations/preview/workspace/`
      );
      expect(urls.pop()).toBe('/api/auth/workspace-session');
      await expect(workspace.startWorkspaceSession('other')).rejects.toThrow('selected runtime');
      expect(await runtime.url('/api/file/download', { path: '/workspace/a' })).toBe(
        `${host}/api/conversations/preview/file/download?path=%2Fworkspace%2Fa`
      );
    } finally {
      scoped = false;
    }
  });
  it('binds created, loaded and forked workspaces to their own runtimes', async () => {
    scoped = true;
    try {
      const manager = new ConversationManager({ host });
      const created = await manager.createConversation({ kind: 'Agent' } as AgentBase, {
        workingDir: '/workspace',
      });
      expect(created.workspace.runtime?.conversationId).toBe('created');
      await created.workspace.executeCommand('pwd');
      expect(urls.pop()).toBe('/api/conversations/created/bash/execute_bash_command');
      const loaded = await manager.loadConversation('created', '/workspace');
      expect(loaded.workspace.connection).toBe(created.workspace.connection);
      const forked = await created.fork();
      expect(forked.workspace.runtime?.conversationId).toBe('forked');
      await forked.workspace.executeCommand('pwd');
      expect(urls.pop()).toBe('/api/conversations/forked/bash/execute_bash_command');
      expect(created.workspace.runtime?.conversationId).toBe('created');
    } finally {
      scoped = false;
    }
  });
});
