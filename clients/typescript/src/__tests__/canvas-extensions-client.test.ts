import { CanvasExtensionsClient } from '../client/canvas-extensions-client';

const originalFetch = global.fetch;

describe('CanvasExtensionsClient', () => {
  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('calls the typed backend lifecycle endpoints', async () => {
    global.fetch = vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            name: 'managed-vscode',
            state: 'stopped',
            revision: 'sha-1',
            prepared_revision: null,
            pid: null,
            port: null,
            detail: null,
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )
    ) as typeof fetch;
    const client = new CanvasExtensionsClient({
      host: 'http://example.com/',
      apiKey: 'secret',
    });

    await client.getBackendStatus('managed/vscode');
    await client.prepareBackend('managed-vscode', 'sha-1');
    await client.startBackend('managed-vscode', 'sha-1');
    await client.stopBackend('managed-vscode');
    await client.getBackendLogs('managed-vscode', 1024);
    await client.deleteBackendData('managed-vscode');

    const calls = (global.fetch as ReturnType<typeof vi.fn>).mock.calls;
    expect(calls.map(([url]) => url)).toEqual([
      'http://example.com/api/canvas-extensions/installed/managed%2Fvscode/backend',
      'http://example.com/api/canvas-extensions/installed/managed-vscode/backend/prepare',
      'http://example.com/api/canvas-extensions/installed/managed-vscode/backend/start',
      'http://example.com/api/canvas-extensions/installed/managed-vscode/backend/stop',
      'http://example.com/api/canvas-extensions/installed/managed-vscode/backend/logs?limit_bytes=1024',
      'http://example.com/api/canvas-extensions/installed/managed-vscode/backend/data',
    ]);
    expect(JSON.parse(calls[1][1].body as string)).toEqual({ revision: 'sha-1' });
    expect(JSON.parse(calls[2][1].body as string)).toEqual({ revision: 'sha-1' });
    expect(calls.every(([, init]) => init.headers['X-Session-API-Key'] === 'secret')).toBe(true);
  });
});
