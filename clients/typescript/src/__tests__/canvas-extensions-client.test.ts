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

  it('bootstraps and revokes app sessions on the discovered ingress', async () => {
    global.fetch = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ingress_url: 'https://apps.example.test/app-backends/demo/',
            expires_at: '2026-09-23T15:00:00Z',
            iframe_sandbox: 'allow-scripts',
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 })) as typeof fetch;
    const client = new CanvasExtensionsClient({
      host: 'https://agent.example.test',
      appBackendIngressUrl: 'https://apps.example.test/',
      apiKey: 'control-secret',
    });

    const session = await client.createAppBackendSession('demo/app');
    await client.revokeAppBackendSession('demo/app');

    expect(session.ingress_url).toBe('https://apps.example.test/app-backends/demo/');
    const calls = (global.fetch as ReturnType<typeof vi.fn>).mock.calls;
    expect(calls.map(([url]) => url)).toEqual([
      'https://apps.example.test/app-backends/demo%2Fapp/session',
      'https://apps.example.test/app-backends/demo%2Fapp/session',
    ]);
    expect(calls.every(([, init]) => init.credentials === 'include')).toBe(true);
    expect(calls.every(([, init]) => init.headers['X-Session-API-Key'] === 'control-secret')).toBe(
      true
    );
    expect(calls.every(([url]) => !url.includes('control-secret'))).toBe(true);
  });

  it('fails closed when app backend ingress discovery is unavailable', async () => {
    const client = new CanvasExtensionsClient({ host: 'https://agent.example.test' });

    await expect(client.createAppBackendSession('demo')).rejects.toThrow(
      'use app_backend_ingress_url from /server_info'
    );
  });
});
