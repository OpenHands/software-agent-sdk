import { createServer, Server } from 'node:http';
import { AddressInfo } from 'node:net';
import { ServerClient } from '../client/server-client';
import { HttpClient } from '../client/http-client';
import { RemoteWorkspace } from '../workspace/remote-workspace';

describe('conversation-scoped requests', () => {
  let server: Server;
  let host: string;
  const urls: string[] = [];
  let scoped = false;
  beforeAll(async () => {
    server = createServer((req, res) => {
      urls.push(req.url!);
      if (req.url === '/server_info') {
        res.setHeader('content-type', 'application/json');
        res.end(JSON.stringify({ capabilities: scoped ? ['conversation_runtime_routes_v1'] : [] }));
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
  it('uses canonical routes and preserves explicit context on capable servers', async () => {
    scoped = true;
    try {
      const client = new HttpClient({ baseUrl: host, conversationId: 'default' });
      await client.get('/api/file/download', { params: { cid: 'explicit', path: '/workspace/a' } });
      expect(urls.pop()).toBe('/api/conversations/explicit/file/download?path=%2Fworkspace%2Fa');
      await client.post('/api/bash/execute_bash_command', { command: 'pwd' });
      expect(urls.pop()).toBe('/api/conversations/default/bash/execute_bash_command');
      await client.get('/api/tools/');
      expect(urls.pop()).toBe('/api/tools/');
      await client.get('/api/file/home');
      expect(urls.pop()).toBe('/api/file/home');
      await client.post('/api/mcp/test', { server: {} });
      expect(urls.pop()).toBe('/api/conversations/default/mcp/test');
      await client.get('/api/mcp/oauth/status/job');
      expect(urls.pop()).toBe('/api/mcp/oauth/status/job');
    } finally {
      scoped = false;
    }
  });
  it('preserves unscoped behavior and explicit query context', async () => {
    await new HttpClient({ baseUrl: host }).get('/api/file/home');
    expect(urls.pop()).toBe('/api/file/home');
    await new HttpClient({ baseUrl: host, conversationId: 'default' }).get('/api/file/download', {
      params: { cid: 'explicit' },
    });
    expect(urls.pop()).toBe('/api/file/download?cid=explicit');
  });
});
