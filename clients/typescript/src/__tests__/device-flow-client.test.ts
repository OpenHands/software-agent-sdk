import { getEventListeners } from 'node:events';
import type { MockedFunction } from 'vitest';
import { CloudClient, pollForToken } from '../clients';

const originalFetch = global.fetch;

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
}

function requestHeadersForCall(callIndex = 0): Headers {
  const mockFetch = global.fetch as MockedFunction<typeof fetch>;
  return new Headers(mockFetch.mock.calls[callIndex]?.[1]?.headers);
}

describe('device flow request metadata', () => {
  afterEach(() => {
    global.fetch = originalFetch;
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('releases abort listeners after successful polling intervals', async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    global.fetch = vi
      .fn()
      .mockImplementationOnce(() =>
        Promise.resolve(new Response('{"error":"authorization_pending"}', { status: 400 }))
      )
      .mockImplementationOnce(() =>
        Promise.resolve(new Response('{"error":"authorization_pending"}', { status: 400 }))
      )
      .mockResolvedValue(jsonResponse({ access_token: 'token' })) as typeof fetch;

    const result = pollForToken('https://cloud.example.com', 'device-code', {
      interval: 1,
      signal: controller.signal,
    });
    await vi.advanceTimersByTimeAsync(2000);

    await expect(result).resolves.toMatchObject({ access_token: 'token' });
    expect(getEventListeners(controller.signal, 'abort')).toHaveLength(0);
  });

  it('cancels the active polling interval and releases its timer', async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    global.fetch = vi
      .fn()
      .mockImplementation(() =>
        Promise.resolve(new Response('{"error":"authorization_pending"}', { status: 400 }))
      ) as typeof fetch;

    const result = pollForToken('https://cloud.example.com', 'device-code', {
      interval: 1,
      signal: controller.signal,
    });
    const cancelled = expect(result).rejects.toMatchObject({ code: 'cancelled' });
    await vi.advanceTimersByTimeAsync(0);
    controller.abort();

    await cancelled;
    expect(vi.getTimerCount()).toBe(0);
    expect(getEventListeners(controller.signal, 'abort')).toHaveLength(0);
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });

  it('releases completed interval listeners when polling times out', async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    global.fetch = vi
      .fn()
      .mockImplementation(() =>
        Promise.resolve(new Response('{"error":"authorization_pending"}', { status: 400 }))
      ) as typeof fetch;

    const result = pollForToken('https://cloud.example.com', 'device-code', {
      interval: 1,
      timeout: 2000,
      signal: controller.signal,
    });
    const timedOut = expect(result).rejects.toMatchObject({ code: 'timeout' });
    await vi.advanceTimersByTimeAsync(2000);

    await timedOut;
    expect(getEventListeners(controller.signal, 'abort')).toHaveLength(0);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('CloudClient forwards additional headers when starting authorization', async () => {
    global.fetch = vi.fn().mockResolvedValue(
      jsonResponse({
        device_code: 'device-code',
        user_code: 'user-code',
        verification_uri: 'https://cloud.example.com/device',
        expires_in: 600,
        interval: 5,
      })
    ) as typeof fetch;
    const client = new CloudClient({ host: 'https://cloud.example.com' });

    await client.startDeviceFlow({
      headers: {
        'content-type': 'text/plain',
        'X-OpenHands-Client': 'agent_canvas',
      },
    });

    expect(global.fetch).toHaveBeenCalledWith(
      'https://cloud.example.com/oauth/device/authorize',
      expect.objectContaining({ method: 'POST' })
    );
    const headers = requestHeadersForCall();
    expect(headers.get('Content-Type')).toBe('application/json');
    expect(headers.get('X-OpenHands-Client')).toBe('agent_canvas');
  });

  it('forwards additional headers while polling for a token', async () => {
    global.fetch = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ access_token: 'token', token_type: 'Bearer' })
      ) as typeof fetch;

    await pollForToken('https://cloud.example.com', 'device-code', {
      interval: 1,
      headers: {
        'CONTENT-TYPE': 'text/plain',
        'X-OpenHands-Client-Version': '1.4.0',
      },
    });

    expect(global.fetch).toHaveBeenCalledWith(
      'https://cloud.example.com/oauth/device/token',
      expect.objectContaining({ method: 'POST' })
    );
    const headers = requestHeadersForCall();
    expect(headers.get('Content-Type')).toBe('application/x-www-form-urlencoded');
    expect(headers.get('X-OpenHands-Client-Version')).toBe('1.4.0');
  });
});
