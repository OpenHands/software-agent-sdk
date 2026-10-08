import { afterEach, expect, it, vi } from 'vitest';
import { CodexAuthClient } from '../client/codex-auth-client';

afterEach(() => vi.unstubAllGlobals());

it('sends only opaque login handles and uses the configured server session key', async () => {
  const calls: Array<{ path: string; method: string; body: unknown; key: string | null }> = [];
  vi.stubGlobal('fetch', async (url: string, init: RequestInit) => {
    calls.push({
      path: new URL(url).pathname,
      method: init.method ?? 'GET',
      body: init.body ? JSON.parse(String(init.body)) : null,
      key: new Headers(init.headers).get('X-Session-API-Key'),
    });
    return new Response(JSON.stringify({ connected: false, state: 'pending', expires_at: null }), {
      headers: { 'Content-Type': 'application/json' },
    });
  });
  const client = new CodexAuthClient({ host: 'https://server.example', apiKey: 'session-key' });
  await client.getStatus();
  await client.startDeviceLogin();
  await client.pollDeviceLogin('opaque-handle');
  await client.cancelDeviceLogin('opaque-handle');
  await client.logout();
  client.close();
  expect(calls.map(({ path, method, body }) => ({ path, method, body }))).toEqual([
    { path: '/api/acp/codex/auth/status', method: 'GET', body: null },
    { path: '/api/acp/codex/auth/device/start', method: 'POST', body: null },
    {
      path: '/api/acp/codex/auth/device/poll',
      method: 'POST',
      body: { device_code: 'opaque-handle' },
    },
    {
      path: '/api/acp/codex/auth/device/cancel',
      method: 'POST',
      body: { device_code: 'opaque-handle' },
    },
    { path: '/api/acp/codex/auth/logout', method: 'POST', body: null },
  ]);
  expect(calls.every(({ key }) => key === 'session-key')).toBe(true);
});
