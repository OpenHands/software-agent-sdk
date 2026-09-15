import { createServer, Server } from 'node:http';
import { AddressInfo } from 'node:net';
import { ConversationManager } from '../conversation/conversation-manager';
import { FileClient } from '../client/file-client';
import { HttpClient } from '../client/http-client';
import { BashClient } from '../client/bash-client';
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
      if (req.method === 'DELETE' && req.url === '/api/auth/workspace-session') {
        res.writeHead(204).end();
        return;
      }
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
  beforeEach(() => {
    urls.length = 0;
    scoped = true;
    discoveryStatus = 200;
  });
  it.each([false, true])('routes workspace commands with scoped support=%s', async (supported) => {
    scoped = supported;
    const workspace = new RemoteWorkspace({
      host,
      workingDir: '/workspace',
      conversationId: 'selected',
    });
    const result = await workspace.executeCommand('pwd');
    expect(result.stdout).toBe('ok');
    expect(urls.pop()).toBe(
      supported
        ? '/api/conversations/selected/bash/execute_bash_command'
        : '/api/bash/execute_bash_command?cid=selected'
    );
  });
  it('scopes workspace operations while keeping setup operations global', async () => {
    const options = { host, conversationId: 'selected' };
    const files = new FileClient(options);
    await files.downloadFile('/workspace/a');
    expect(urls.pop()).toBe('/api/conversations/selected/file/download?path=%2Fworkspace%2Fa');
    await files.getHome();
    expect(urls.pop()).toBe('/api/file/home');
    await new BashClient(options).executeCommand({ command: 'pwd' });
    expect(urls.pop()).toBe('/api/conversations/selected/bash/execute_bash_command');
    await new RemoteWorkspace({ ...options, workingDir: '/workspace' }).gitChanges('/workspace');
    expect(urls.pop()).toBe('/api/conversations/selected/git/changes?path=%2Fworkspace');
  });
  it('rejects requests that override the selected conversation', async () => {
    const workspace = new RemoteWorkspace({
      host,
      workingDir: '/workspace',
      conversationId: 'selected',
    });
    await expect(
      workspace.client.get('/api/file/download', { params: { cid: 'other' } })
    ).rejects.toThrow('cannot be overridden');
    await expect(workspace.client.get('/api/file/../../settings')).rejects.toThrow('API path');
    expect(
      () => new RemoteWorkspace({ host, workingDir: '/workspace', conversationId: '' })
    ).toThrow('conversation ID');
    expect(urls).toEqual([]);
  });
  it('keeps raw server requests unscoped and workspace requests on the host', async () => {
    await new HttpClient({ baseUrl: host }).get('/api/file/download', {
      params: { cid: 'explicit' },
    });
    await new FileClient({ host }).downloadFile('/workspace/a');
    await new RemoteWorkspace({ host, workingDir: '/workspace' }).executeCommand('pwd');
    expect(urls).toEqual([
      '/api/file/download?cid=explicit',
      '/api/host/file/download?path=%2Fworkspace%2Fa',
      '/api/host/bash/execute_bash_command',
    ]);
  });
  it('reuses existing discovery for concurrent requests on a client', async () => {
    const files = new FileClient({ host, conversationId: 'selected' });
    await Promise.all([files.downloadFile('/workspace/a'), files.downloadFile('/workspace/b')]);
    expect(urls.filter((url) => url === '/server_info')).toHaveLength(1);
    expect(urls).toContain('/api/conversations/selected/file/download?path=%2Fworkspace%2Fa');
    expect(urls).toContain('/api/conversations/selected/file/download?path=%2Fworkspace%2Fb');
  });
  it('retries failed discovery without silently downgrading', async () => {
    discoveryStatus = 503;
    const files = new FileClient({ host, conversationId: 'retry' });
    await expect(files.downloadFile('/workspace/a')).rejects.toMatchObject({ status: 503 });
    expect(urls).toEqual(['/server_info']);
    discoveryStatus = 200;
    await files.downloadFile('/workspace/a');
    expect(urls.pop()).toBe('/api/conversations/retry/file/download?path=%2Fworkspace%2Fa');
  });
  it('supports older servers without server info', async () => {
    discoveryStatus = 404;
    const files = new FileClient({ host, conversationId: 'selected' });
    await files.downloadFile('/workspace/a');
    expect(urls.pop()).toBe('/api/file/download?path=%2Fworkspace%2Fa&cid=selected');
  });
  it('keeps workspace session authentication global and identity fixed', async () => {
    const workspace = new RemoteWorkspace({
      host,
      workingDir: '/workspace',
      conversationId: 'preview',
    });
    expect(await workspace.startWorkspaceSession('preview')).toBe(
      `${host}/api/conversations/preview/workspace/`
    );
    expect(urls.pop()).toBe('/api/auth/workspace-session');
    await expect(workspace.startWorkspaceSession('other')).rejects.toThrow('selected runtime');
    await workspace.deleteWorkspaceSession();
    expect(urls.pop()).toBe('/api/auth/workspace-session');
  });
  it('binds created, loaded and forked workspaces to their conversations', async () => {
    const manager = new ConversationManager({ host });
    const created = await manager.createConversation({ kind: 'Agent' } as AgentBase, {
      workingDir: '/workspace',
    });
    await created.workspace.executeCommand('pwd');
    expect(urls.pop()).toBe('/api/conversations/created/bash/execute_bash_command');
    const loaded = await manager.loadConversation('created', '/workspace');
    await loaded.workspace.executeCommand('pwd');
    expect(urls.pop()).toBe('/api/conversations/created/bash/execute_bash_command');
    const forked = await created.fork();
    await forked.workspace.executeCommand('pwd');
    expect(urls.pop()).toBe('/api/conversations/forked/bash/execute_bash_command');
    await created.workspace.executeCommand('pwd');
    expect(urls.pop()).toBe('/api/conversations/created/bash/execute_bash_command');
  });
});
