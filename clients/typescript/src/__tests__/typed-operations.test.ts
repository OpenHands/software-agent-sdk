import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { ConversationClient } from '../client/conversation-client';
import { FileClient } from '../client/file-client';
import { GitClient } from '../client/git-client';
import { HttpError } from '../client/http-client';
import { InitClient } from '../client/init-client';
import { PluginsClient } from '../client/plugins-client';

type Recorded = {
  method?: string;
  url: string;
  body: string;
  headers: Record<string, string | string[] | undefined>;
};

function jsonResponse(res: import('node:http').ServerResponse, status: number, body: unknown) {
  res.setHeader('content-type', 'application/json');
  res.writeHead(status).end(JSON.stringify(body));
}

describe('typed clients for the seven uncovered operations (real HTTP)', () => {
  let server: Server;
  let host: string;
  let requests: Recorded[];
  let failPath: string | undefined;

  beforeEach(async () => {
    requests = [];
    failPath = undefined;
    server = createServer((req, res) => {
      const chunks: Buffer[] = [];
      req.on('data', (c: Buffer) => chunks.push(c));
      req.on('end', () => {
        const url = new URL(req.url ?? '/', 'http://localhost');
        const body = Buffer.concat(chunks).toString('utf8');
        requests.push({
          method: req.method,
          url: url.pathname + url.search,
          body,
          headers: req.headers,
        });
        if (failPath && url.pathname === failPath) {
          jsonResponse(res, 400, { detail: 'bad request' });
          return;
        }
        switch (url.pathname) {
          case '/api/conversations/conv-1/events/batch': {
            const ids = JSON.parse(body) as string[];
            jsonResponse(
              res,
              200,
              ids.map((id) => (id === 'missing' ? null : { id, kind: 'MessageEvent' }))
            );
            return;
          }
          case '/api/conversations/conv-1/load_plugin':
            jsonResponse(res, 200, { success: true });
            return;
          case '/api/git/commits':
            jsonResponse(res, 200, {
              commits: [
                { sha: 'abc123', short_sha: 'abc123', author: 'A', subject: 's', timestamp: 't' },
              ],
              has_more: false,
            });
            return;
          case '/api/git/commits/abc123/changes':
            jsonResponse(res, 200, [{ path: 'a.ts', status: 'ADDED' }]);
            return;
          case '/api/file/archive': {
            res.setHeader('content-type', 'application/gzip');
            res.writeHead(200).end(new Uint8Array([31, 139, 8, 1, 2, 3]));
            return;
          }
          case '/api/init':
            if (req.method === 'GET') jsonResponse(res, 200, { state: 'dormant', error: null });
            else jsonResponse(res, 200, { state: 'ready', error: null });
            return;
          default:
            jsonResponse(res, 404, { detail: 'not found' });
        }
      });
    });
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    host = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });

  afterEach(async () => {
    server.closeAllConnections();
    await new Promise<void>((resolve, reject) => server.close((e) => (e ? reject(e) : resolve())));
  });

  it('batchGetEvents POSTs bare ids (browser-safe, no GET body) and preserves order/nulls', async () => {
    const client = new ConversationClient({ host, apiKey: 'sess' });
    const result = await client.batchGetEvents('conv-1', ['e1', 'missing', 'e2']);
    expect(result.map((e) => (e ? e.id : null))).toEqual(['e1', null, 'e2']);
    expect(requests).toHaveLength(1);
    expect(requests[0].method).toBe('POST');
    expect(requests[0].url).toBe('/api/conversations/conv-1/events/batch');
    expect(JSON.parse(requests[0].body)).toEqual(['e1', 'missing', 'e2']);
    expect(requests[0].headers['x-session-api-key']).toBe('sess');
  });

  it('batchGetEvents handles empty ids and propagates server errors', async () => {
    const client = new ConversationClient({ host });
    await expect(client.batchGetEvents('conv-1', [])).resolves.toEqual([]);
    failPath = '/api/conversations/conv-1/events/batch';
    await expect(client.batchGetEvents('conv-1', ['e1'])).rejects.toMatchObject({
      constructor: HttpError,
      status: 400,
    });
  });

  it('encodes conversation ids in batch and load_plugin paths', async () => {
    const client = new ConversationClient({ host });
    await expect(client.loadPlugin('conv-1', 'my-plugin')).resolves.toEqual({ success: true });
    expect(requests[0].method).toBe('POST');
    expect(requests[0].url).toBe('/api/conversations/conv-1/load_plugin');
    expect(JSON.parse(requests[0].body)).toEqual({ plugin_ref: 'my-plugin' });

    const plugins = new PluginsClient({ host });
    await expect(plugins.loadPluginForConversation('conv-1', 'p2')).resolves.toEqual({
      success: true,
    });
    expect(requests[1].url).toBe('/api/conversations/conv-1/load_plugin');
  });

  it('loadPlugin propagates 400/404 as HttpError', async () => {
    failPath = '/api/conversations/conv-1/load_plugin';
    const client = new ConversationClient({ host });
    await expect(client.loadPlugin('conv-1', 'nope')).rejects.toMatchObject({ status: 400 });
  });

  it('GitClient getCommits/getCommitChanges hit global routes with typed shapes', async () => {
    const client = new GitClient({ host, apiKey: 'sess' });
    const page = await client.getCommits('/repo', { limit: 5 });
    expect(page.commits[0].sha).toBe('abc123');
    expect(page.has_more).toBe(false);
    expect(requests[0].url).toBe('/api/git/commits?path=%2Frepo&limit=5');

    const changes = await client.getCommitChanges('/repo', 'abc123');
    expect(changes).toEqual([{ path: 'a.ts', status: 'ADDED' }]);
    expect(requests[1].url).toBe('/api/git/commits/abc123/changes?path=%2Frepo');

    failPath = '/api/git/commits';
    await expect(client.getCommits('/repo')).rejects.toMatchObject({ status: 400 });
  });

  it('FileClient downloadArchive returns Blob and ArrayBuffer with query params', async () => {
    const client = new FileClient({ host });
    const blob = await client.downloadArchive('/ws dir', {
      format: 'tar.gz',
      useDefaultExcludes: false,
      exclude: ['*.bin', 'dist'],
    });
    expect(blob).toBeInstanceOf(Blob);
    expect(new Uint8Array(await blob.arrayBuffer())).toEqual(new Uint8Array([31, 139, 8, 1, 2, 3]));
    expect(requests[0].url).toBe(
      '/api/file/archive?path=%2Fws+dir&format=tar.gz&use_default_excludes=false&exclude=*.bin&exclude=dist'
    );

    const buf = await client.downloadArchiveBuffer('/ws', { baseRef: 'main' });
    expect(buf).toBeInstanceOf(ArrayBuffer);
    expect(new Uint8Array(buf)).toEqual(new Uint8Array([31, 139, 8, 1, 2, 3]));
    expect(requests[1].url).toBe('/api/file/archive?path=%2Fws&base_ref=main');

    failPath = '/api/file/archive';
    await expect(client.downloadArchive('/nope')).rejects.toMatchObject({ status: 400 });
  });

  it('InitClient getStatus and initialize use X-Init-API-Key distinct from session key', async () => {
    const client = new InitClient({ host, apiKey: 'session-key' });
    await expect(client.getStatus()).resolves.toEqual({ state: 'dormant', error: null });
    expect(requests[0].method).toBe('GET');
    expect(requests[0].url).toBe('/api/init');

    await expect(
      client.initialize({ session_api_keys: ['k1'] }, { initApiKey: 'bootstrap-secret' })
    ).resolves.toEqual({ state: 'ready', error: null });
    expect(requests[1].method).toBe('POST');
    expect(JSON.parse(requests[1].body)).toEqual({ session_api_keys: ['k1'] });
    expect(requests[1].headers['x-init-api-key']).toBe('bootstrap-secret');
    expect(requests[1].headers['x-session-api-key']).toBe('session-key');

    failPath = '/api/init';
    await expect(client.initialize({}, { initApiKey: 'wrong' })).rejects.toMatchObject({
      status: 400,
    });
  });
});
