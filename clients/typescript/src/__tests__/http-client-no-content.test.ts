import { HttpClient } from '../client/http-client';

describe('HTTP no-content responses', () => {
  afterEach(() => vi.unstubAllGlobals());

  it.each(['auto', 'json'] as const)('accepts a 204 response in %s mode', async (responseType) => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response(null, { status: 204, headers: { 'Content-Type': 'application/json' } })
        )
    );
    const client = new HttpClient({ baseUrl: 'http://example.com' });

    const response = await client.delete('/value', { responseType });

    expect(response.status).toBe(204);
    expect(response.data).toBeUndefined();
  });

  it('still rejects invalid JSON in a response that has content', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response('not json', { headers: { 'Content-Type': 'application/json' } })
        )
    );
    const client = new HttpClient({ baseUrl: 'http://example.com' });

    await expect(client.get('/value')).rejects.toThrow('Request failed');
  });
});
