import { RemoteConversation } from '../conversation/remote-conversation';
import { createEmptyHookConfig } from '../hooks';
import type { HooksResponse } from '../models/api';
import type { AgentBase } from '../types/base';
import { RemoteWorkspace } from '../workspace/remote-workspace';

describe('RemoteConversation hook loading', () => {
  const agent = { kind: 'Agent' } as AgentBase;
  const workspace = new RemoteWorkspace({
    host: 'http://example.com',
    workingDir: '/workspace',
  });
  const conversation = new RemoteConversation(agent, workspace);

  afterEach(() => vi.unstubAllGlobals());

  function mockHooksResponse(body: HooksResponse) {
    const fetchMock = vi.fn().mockImplementation(
      async () =>
        new Response(JSON.stringify(body), {
          headers: { 'content-type': 'application/json' },
        })
    );
    vi.stubGlobal('fetch', fetchMock);
    return fetchMock;
  }

  it('returns usable hooks and source errors together, and keeps the legacy return value', async () => {
    const response: HooksResponse = {
      hook_config: { ...createEmptyHookConfig(), stop: [{ hooks: [{ command: 'check.sh' }] }] },
      errors: [
        { source: 'user', path: '/home/user/.openhands/hooks.json', message: 'Invalid JSON' },
      ],
    };
    const fetchMock = mockHooksResponse(response);

    await expect(conversation.loadHooksWithErrors('/other')).resolves.toEqual(response);
    await expect(conversation.loadHooks()).resolves.toEqual(response.hook_config);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'http://example.com/api/hooks',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ project_dir: '/other' }) })
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'http://example.com/api/hooks',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ project_dir: '/workspace' }),
      })
    );
  });

  it('preserves an older server response that omits load diagnostics', async () => {
    mockHooksResponse({ hook_config: null });

    await expect(conversation.loadHooksWithErrors()).resolves.toEqual({ hook_config: null });
    await expect(conversation.loadHooks()).resolves.toBeNull();
  });
});
