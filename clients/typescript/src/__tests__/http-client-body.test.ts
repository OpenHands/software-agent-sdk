import { HttpClient } from '../client/http-client';

describe('HTTP JSON request bodies', () => {
  afterEach(() => vi.unstubAllGlobals());

  it.each([false, 0, ''])('preserves the JSON value %j', async (data) => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}'));
    vi.stubGlobal('fetch', fetch);
    const client = new HttpClient({ baseUrl: 'http://example.com' });

    await client.post('/value', data);

    expect(fetch).toHaveBeenCalledWith(
      'http://example.com/value',
      expect.objectContaining({ body: JSON.stringify(data) })
    );
  });

  it('leaves an omitted body unset', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{}'));
    vi.stubGlobal('fetch', fetch);
    const client = new HttpClient({ baseUrl: 'http://example.com' });

    await client.post('/value');

    expect(fetch.mock.calls[0][1].body).toBeUndefined();
  });
});
