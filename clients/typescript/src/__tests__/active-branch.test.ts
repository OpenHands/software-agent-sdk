import { afterEach, describe, expect, it, vi } from 'vitest';
import { ConversationClient } from '../client/conversation-client';

afterEach(() => vi.unstubAllGlobals());

describe('ConversationClient.getActiveBranch', () => {
  it('uses the authoritative endpoint with an optional limit', async () => {
    const events = [
      {
        id: 'active-message',
        kind: 'MessageEvent',
        source: 'user',
        llm_message: { role: 'user', content: [] },
      },
    ];
    const fetch = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify(events), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      );
    vi.stubGlobal('fetch', fetch);
    const client = new ConversationClient({ host: 'http://example.com' });
    await expect(client.getActiveBranch('c1', { limit: 10 })).resolves.toEqual(events);
    expect(fetch).toHaveBeenCalledWith(
      'http://example.com/api/conversations/c1/events/active-branch?limit=10',
      expect.objectContaining({ method: 'GET' })
    );
  });

  it('preserves an explicitly empty branch', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response('[]', { status: 200, headers: { 'content-type': 'application/json' } })
        )
    );
    const client = new ConversationClient({ host: 'http://example.com' });
    await expect(client.getActiveBranch('c1')).resolves.toEqual([]);
  });

  it.each([404, 409])('does not fall back to the full log on HTTP %s', async (status) => {
    const fetch = vi
      .fn()
      .mockImplementation(() =>
        Promise.resolve(new Response('{"detail":"unavailable"}', { status }))
      );
    vi.stubGlobal('fetch', fetch);
    const client = new ConversationClient({ host: 'http://example.com' });
    await expect(client.getActiveBranch('c1')).rejects.toMatchObject({ status });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
