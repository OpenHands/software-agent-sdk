import type { Mock } from 'vitest';
import { HttpClient } from '../client/http-client';
import { RemoteState } from '../conversation/remote-state';

const originalFetch = global.fetch;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

const CONVERSATION_INFO = {
  execution_status: 'running',
  confirmation_policy: { kind: 'NeverConfirm' },
  activated_knowledge_skills: ['skill-a', 'skill-b'],
  agent: { kind: 'Agent', name: 'test-agent' },
  workspace: { kind: 'LocalWorkspace' },
  persistence_dir: '/data/conversations/abc',
};

function makeState(payload: unknown): { state: RemoteState; fetchMock: Mock } {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(payload)) as Mock;
  global.fetch = fetchMock as typeof fetch;
  const client = new HttpClient({ baseUrl: 'http://example.com' });
  return { state: new RemoteState(client, 'abc'), fetchMock };
}

describe('RemoteState full_state normalization', () => {
  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('reads accessor fields from a flat conversation-info payload', async () => {
    const { state } = makeState(CONVERSATION_INFO);

    await expect(state.getExecutionStatus()).resolves.toBe('running');
    await expect(state.getConfirmationPolicy()).resolves.toEqual({ kind: 'NeverConfirm' });
    await expect(state.getActivatedKnowledgeSkills()).resolves.toEqual(['skill-a', 'skill-b']);
    await expect(state.getAgent()).resolves.toEqual({ kind: 'Agent', name: 'test-agent' });
    await expect(state.getWorkspace()).resolves.toEqual({ kind: 'LocalWorkspace' });
    await expect(state.getPersistenceDir()).resolves.toBe('/data/conversations/abc');
    await expect(state.modelDump()).resolves.toMatchObject(CONVERSATION_INFO);
  });

  it('unwraps a full_state-wrapped payload exactly once for every accessor', async () => {
    // The server may wrap the info in `{ full_state: {...} }`. Normalization now
    // happens once in getConversationInfo(); accessors must still read through.
    const { state } = makeState({ full_state: CONVERSATION_INFO });

    await expect(state.getExecutionStatus()).resolves.toBe('running');
    await expect(state.getConfirmationPolicy()).resolves.toEqual({ kind: 'NeverConfirm' });
    await expect(state.getActivatedKnowledgeSkills()).resolves.toEqual(['skill-a', 'skill-b']);
    await expect(state.getAgent()).resolves.toEqual({ kind: 'Agent', name: 'test-agent' });
    await expect(state.getWorkspace()).resolves.toEqual({ kind: 'LocalWorkspace' });
    await expect(state.getPersistenceDir()).resolves.toBe('/data/conversations/abc');
  });

  it('falls back to the legacy agent_status field when execution_status is absent', async () => {
    const { state } = makeState({ agent_status: 'idle' });
    await expect(state.getExecutionStatus()).resolves.toBe('idle');
  });

  it('throws a descriptive error when a required field is missing', async () => {
    const { state } = makeState({ execution_status: 'running' });
    await expect(state.getPersistenceDir()).rejects.toThrow(/persistence_dir missing/);
  });

  it.each([
    { key: 'full_state', wrapped: false },
    { key: 'full_state', wrapped: true },
    { key: '__full_state__', wrapped: false },
    { key: '__full_state__', wrapped: true },
  ])('applies deltas after a $key snapshot (wrapped: $wrapped)', async ({ key, wrapped }) => {
    const { state, fetchMock } = makeState(CONVERSATION_INFO);

    await state.updateStateFromEvent({
      id: 'evt-1',
      kind: 'ConversationStateUpdateEvent',
      timestamp: '2024-01-01T00:00:00Z',
      key,
      value: wrapped ? { full_state: CONVERSATION_INFO } : CONVERSATION_INFO,
    });

    await expect(state.getExecutionStatus()).resolves.toBe('running');
    await expect(state.getConfirmationPolicy()).resolves.toEqual({ kind: 'NeverConfirm' });

    for (const update of [
      { key: 'execution_status', value: 'paused' },
      { key: 'confirmation_policy', value: { kind: 'AlwaysConfirm' } },
    ]) {
      await state.updateStateFromEvent({
        id: update.key,
        kind: 'ConversationStateUpdateEvent',
        timestamp: '2024-01-01T00:00:01Z',
        ...update,
      });
    }

    await expect(state.getExecutionStatus()).resolves.toBe('paused');
    await expect(state.getConfirmationPolicy()).resolves.toEqual({ kind: 'AlwaysConfirm' });
    await expect(state.getPersistenceDir()).resolves.toBe('/data/conversations/abc');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(['full_state', '__full_state__'])(
    'rejects non-object %s events without corrupting the cache',
    async (key) => {
      const { state, fetchMock } = makeState(CONVERSATION_INFO);
      await expect(state.getExecutionStatus()).resolves.toBe('running');

      for (const value of ['not-an-object', null, []]) {
        await expect(
          state.updateStateFromEvent({
            id: 'evt-2',
            kind: 'ConversationStateUpdateEvent',
            timestamp: '2024-01-01T00:00:00Z',
            key,
            value,
          })
        ).rejects.toThrow('Full conversation state update must contain an object value.');

        await expect(state.getExecutionStatus()).resolves.toBe('running');
      }
      expect(fetchMock).toHaveBeenCalledOnce();
    }
  );

  it.each([false, true])(
    'preserves a newer event during a queued refresh (initial update fails: %s)',
    async (initialUpdateFails) => {
      const { state, fetchMock } = makeState(CONVERSATION_INFO);
      let resolveResponse!: (response: Response) => void;
      const response = new Promise<Response>((resolve) => {
        resolveResponse = resolve;
      });
      const fetchStarted = new Promise<void>((resolve) => {
        fetchMock.mockImplementationOnce(() => {
          resolve();
          return response;
        });
      });

      const initialUpdate = state.updateStateFromEvent({
        id: 'initial-update',
        kind: 'ConversationStateUpdateEvent',
        timestamp: '2024-01-01T00:00:00Z',
        key: initialUpdateFails ? '__full_state__' : 'execution_status',
        value: initialUpdateFails ? 'not-an-object' : 'running',
      });
      const initialResult = initialUpdate.catch((error: unknown) => error);
      const refresh = state.refresh();
      await fetchStarted;

      const newerUpdate = state.updateStateFromEvent({
        id: 'newer-update',
        kind: 'ConversationStateUpdateEvent',
        timestamp: '2024-01-01T00:00:01Z',
        key: 'execution_status',
        value: 'paused',
      });
      const latestUpdate = state.updateStateFromEvent({
        id: 'latest-update',
        kind: 'ConversationStateUpdateEvent',
        timestamp: '2024-01-01T00:00:02Z',
        key: 'execution_status',
        value: 'finished',
      });
      resolveResponse(jsonResponse(CONVERSATION_INFO));
      await Promise.all([refresh, newerUpdate, latestUpdate]);

      if (initialUpdateFails) {
        expect(await initialResult).toMatchObject({
          message: 'Full conversation state update must contain an object value.',
        });
      } else {
        expect(await initialResult).toBeUndefined();
      }
      expect(fetchMock).toHaveBeenCalledOnce();
      await expect(state.getExecutionStatus()).resolves.toBe('finished');
    }
  );
});
