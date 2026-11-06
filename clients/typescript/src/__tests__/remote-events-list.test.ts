import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { RemoteEventsList } from '../events/remote-events-list';
import type { Event } from '../types/base';

function event(id: string): Event {
  return { id, kind: 'MessageEvent', timestamp: '2026-10-03T00:00:00Z' };
}

function page(ids: string[], next_page_id?: string): Response {
  return new Response(JSON.stringify({ items: ids.map(event), next_page_id }), {
    headers: { 'content-type': 'application/json' },
  });
}

describe('RemoteEventsList.getEvents', () => {
  const events = () => new RemoteEventsList({ baseUrl: 'https://agent.example.test' }, 'c1');

  afterEach(() => vi.unstubAllGlobals());

  it('bounds pagination and requests only the remaining budget', async () => {
    const first = Array.from({ length: 100 }, (_, i) => String(i));
    const second = Array.from({ length: 50 }, (_, i) => String(i + 100));
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(page(first, 'next'))
      .mockResolvedValueOnce(page(second, 'more'))
      .mockResolvedValueOnce(page(['150']));
    vi.stubGlobal('fetch', fetch);

    const result = await events().getEvents({ maxEvents: 150 });

    expect(result.map((item) => item.id)).toEqual([...first, ...second]);
    expect(fetch.mock.calls.map(([url]) => url)).toEqual([
      'https://agent.example.test/api/conversations/c1/events/search?limit=100',
      'https://agent.example.test/api/conversations/c1/events/search?limit=50&page_id=next',
    ]);
  });

  it('keeps legacy slicing and deduplicates the WebSocket cache', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockImplementationOnce(() => page(['a', 'b'], 'next'))
        .mockImplementationOnce(() => page(['c']))
    );
    const list = events();
    await list.addEvent(event('b'));
    await list.addEvent(event('d'));
    expect((await list.getEvents(-2)).map((item) => item.id)).toEqual(['c', 'd']);
  });

  it('applies the budget to the merged prefix before slicing', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(page(['a'])));
    const list = events();
    await list.addEvent(event('a'));
    await list.addEvent(event('b'));
    await list.addEvent(event('c'));
    expect(await list.getEvents({ start: 1, maxEvents: 2 })).toEqual([event('b')]);
    expect(await list.length()).toBe(3);
  });

  it('returns no events and makes no request for a zero budget', async () => {
    const fetch = vi.fn().mockResolvedValue(page(['a']));
    vi.stubGlobal('fetch', fetch);
    const list = events();
    await list.addEvent(event('cached'));
    expect(await list.getEvents({ maxEvents: 0 })).toEqual([]);
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each([-1, 1.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1])(
    'rejects invalid maxEvents=%s before requesting',
    async (maxEvents) => {
      const fetch = vi.fn().mockResolvedValue(page([]));
      vi.stubGlobal('fetch', fetch);
      await expect(events().getEvents({ maxEvents })).rejects.toThrow(RangeError);
      expect(fetch).not.toHaveBeenCalled();
    }
  );

  it('rejects a pre-aborted signal without fetching or returning cached events', async () => {
    const fetch = vi.fn().mockResolvedValue(page([]));
    vi.stubGlobal('fetch', fetch);
    const controller = new AbortController();
    controller.abort();
    await expect(events().getEvents({ signal: controller.signal })).rejects.toMatchObject({
      name: 'AbortError',
    });
    expect(fetch).not.toHaveBeenCalled();
  });

  it('cancels an in-flight later page without returning partial results', async () => {
    const controller = new AbortController();
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(page(['a'], 'next'))
      .mockImplementationOnce((_url: string, init: RequestInit) => {
        const signal = init.signal;
        if (!signal) throw new Error('Missing request cancellation signal');
        return new Promise<Response>((_resolve, reject) => {
          signal.addEventListener('abort', () => reject(signal.reason), { once: true });
          controller.abort();
        });
      });
    vi.stubGlobal('fetch', fetch);
    await expect(events().getEvents({ signal: controller.signal })).rejects.toMatchObject({
      name: 'AbortError',
    });
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('stops between pages when cancellation races with a completed response', async () => {
    const controller = new AbortController();
    // Abort while parsing the first response, after fetch has already resolved.
    const response = page(['a'], 'next');
    const json = response.json.bind(response);
    vi.spyOn(response, 'json').mockImplementation(async () => {
      controller.abort();
      return json();
    });
    const fetch = vi.fn().mockResolvedValueOnce(response).mockResolvedValue(page([]));
    vi.stubGlobal('fetch', fetch);
    await expect(events().getEvents({ signal: controller.signal })).rejects.toMatchObject({
      name: 'AbortError',
    });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('propagates a later HTTP failure instead of returning partial results', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(page(['a'], 'next'))
        .mockResolvedValueOnce(new Response('unavailable', { status: 503 }))
    );
    await expect(events().getEvents({ maxEvents: 10 })).rejects.toMatchObject({ status: 503 });
  });
});

it('bounds real HTTP pagination through the public client', async () => {
  const history = Array.from({ length: 250 }, (_, i) => event(String(i)));
  const requests: { path: string; limit: number; key: string | undefined }[] = [];
  const server = createServer((request, response) => {
    const url = new URL(request.url ?? '/', 'http://localhost');
    const offset = Number(url.searchParams.get('page_id') ?? 0);
    const limit = Number(url.searchParams.get('limit'));
    requests.push({
      path: url.pathname,
      limit,
      key: request.headers['x-session-api-key'] as string,
    });
    response.setHeader('content-type', 'application/json');
    response.end(
      JSON.stringify({
        items: history.slice(offset, offset + limit),
        next_page_id: offset + limit < history.length ? String(offset + limit) : undefined,
      })
    );
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  try {
    const list = new RemoteEventsList(
      {
        baseUrl: `http://127.0.0.1:${(server.address() as AddressInfo).port}`,
        apiKey: 'test-session-key',
      },
      'c1'
    );
    expect(await list.getEvents({ maxEvents: 125 })).toEqual(history.slice(0, 125));
    expect(requests).toEqual(
      [100, 25].map((limit) => ({
        path: '/api/conversations/c1/events/search',
        limit,
        key: 'test-session-key',
      }))
    );
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
