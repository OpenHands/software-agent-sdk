import type { Mock } from 'vitest';
import { RemoteWorkspace } from '../index';

const originalFetch = global.fetch;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

describe('RemoteWorkspace git query parameters', () => {
  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('omits ref by default for gitChanges', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([])) as Mock;
    global.fetch = fetchMock as typeof fetch;

    const ws = new RemoteWorkspace({ host: 'http://example.com', workingDir: '/tmp' });
    await ws.gitChanges('/repo');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const url = new URL(fetchMock.mock.calls[0][0] as string);
    expect(url.pathname).toBe('/api/git/changes');
    expect(url.searchParams.get('path')).toBe('/repo');
    expect(url.searchParams.has('ref')).toBe(false);
  });

  it('forwards ref to gitChanges as a query param', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([])) as Mock;
    global.fetch = fetchMock as typeof fetch;

    const ws = new RemoteWorkspace({ host: 'http://example.com', workingDir: '/tmp' });
    await ws.gitChanges('/repo', { ref: 'HEAD' });

    const url = new URL(fetchMock.mock.calls[0][0] as string);
    expect(url.pathname).toBe('/api/git/changes');
    expect(url.searchParams.get('path')).toBe('/repo');
    expect(url.searchParams.get('ref')).toBe('HEAD');
  });

  it('omits ref by default for gitDiff', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ original: '', modified: '' })) as Mock;
    global.fetch = fetchMock as typeof fetch;

    const ws = new RemoteWorkspace({ host: 'http://example.com', workingDir: '/tmp' });
    await ws.gitDiff('/repo/file.ts');

    const url = new URL(fetchMock.mock.calls[0][0] as string);
    expect(url.pathname).toBe('/api/git/diff');
    expect(url.searchParams.get('path')).toBe('/repo/file.ts');
    expect(url.searchParams.has('ref')).toBe(false);
  });

  it('forwards ref to gitDiff as a query param', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ original: '', modified: '' })) as Mock;
    global.fetch = fetchMock as typeof fetch;

    const ws = new RemoteWorkspace({ host: 'http://example.com', workingDir: '/tmp' });
    await ws.gitDiff('/repo/file.ts', { ref: 'abc1234' });

    const url = new URL(fetchMock.mock.calls[0][0] as string);
    expect(url.pathname).toBe('/api/git/diff');
    expect(url.searchParams.get('path')).toBe('/repo/file.ts');
    expect(url.searchParams.get('ref')).toBe('abc1234');
  });
});
