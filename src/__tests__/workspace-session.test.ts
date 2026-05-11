import { RemoteWorkspace } from '../index';

const originalFetch = global.fetch;

function noContentResponse(): Response {
  return new Response(null, { status: 204 });
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

function makeWorkspace(
  opts: {
    host?: string;
    apiKey?: string;
  } = {}
): RemoteWorkspace {
  return new RemoteWorkspace({
    host: opts.host ?? 'https://agent.example.com',
    workingDir: '/workspace',
    apiKey: opts.apiKey ?? 'secret-key',
  });
}

describe('RemoteWorkspace.startWorkspaceSession', () => {
  afterEach(() => {
    global.fetch = originalFetch;
    jest.restoreAllMocks();
  });

  it('mints a workspace session cookie and returns the static asset base URL', async () => {
    const fetchMock = jest.fn().mockResolvedValue(noContentResponse()) as jest.Mock;
    global.fetch = fetchMock as typeof fetch;

    const workspace = makeWorkspace();
    const baseUrl = await workspace.startWorkspaceSession('cid-1234');

    expect(baseUrl).toBe('https://agent.example.com/api/conversations/cid-1234/workspace/');
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const [url, init] = fetchMock.mock.calls[0];
    expect(new URL(url as string).pathname).toBe('/api/auth/workspace-session');
    expect((init as RequestInit).method).toBe('POST');
    expect((init as RequestInit).credentials).toBe('include');

    const headers = (init as RequestInit).headers as Record<string, string>;
    expect(headers['X-Session-API-Key']).toBe('secret-key');
  });

  it('strips a trailing slash on the host so the base URL has exactly one separator', async () => {
    const fetchMock = jest.fn().mockResolvedValue(noContentResponse()) as jest.Mock;
    global.fetch = fetchMock as typeof fetch;

    const workspace = makeWorkspace({ host: 'https://agent.example.com/' });
    const baseUrl = await workspace.startWorkspaceSession('cid-1234');

    expect(baseUrl).toBe('https://agent.example.com/api/conversations/cid-1234/workspace/');
  });

  it('propagates 401s when the session API key is rejected', async () => {
    const fetchMock = jest
      .fn()
      .mockResolvedValue(jsonResponse({ detail: 'Unauthorized' }, 401)) as jest.Mock;
    global.fetch = fetchMock as typeof fetch;

    const workspace = makeWorkspace({ apiKey: 'wrong-key' });
    await expect(workspace.startWorkspaceSession('cid-1234')).rejects.toThrow(/401/);
  });

  it('does not require constructing an Agent or RemoteConversation', async () => {
    // Regression guard for the agent-canvas use case: consumers that only want
    // to embed workspace assets must be able to do so directly from the
    // workspace, without inventing a placeholder Agent + LLM just to satisfy
    // the RemoteConversation constructor. The mere fact this test compiles
    // without importing Agent / RemoteConversation is the assertion.
    const fetchMock = jest.fn().mockResolvedValue(noContentResponse()) as jest.Mock;
    global.fetch = fetchMock as typeof fetch;

    const workspace = makeWorkspace();
    const baseUrl = await workspace.startWorkspaceSession('cid-only');

    expect(baseUrl).toBe('https://agent.example.com/api/conversations/cid-only/workspace/');
  });
});
