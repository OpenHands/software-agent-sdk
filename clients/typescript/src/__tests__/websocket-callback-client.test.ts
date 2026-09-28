import type { WebSocketCallbackClient } from '../events/websocket-client';

class Socket {
  static instances: Socket[] = [];
  readyState = 0;
  onopen?: (event: Event) => void;
  onclose?: (event: CloseEvent) => void;
  onmessage?: (event: MessageEvent) => void;
  send = vi.fn();
  close = vi.fn();
  constructor(readonly url: string) {
    Socket.instances.push(this);
  }
}

describe('typed conversation event transport', () => {
  const originalWindow = (globalThis as { window?: unknown }).window;
  let client: WebSocketCallbackClient;
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(Math, 'random').mockReturnValue(0);
    Socket.instances = [];
    // The transport resolves its WebSocket constructor at import time: browser
    // consumers use window.WebSocket, Node consumers `require('ws')`.
    (globalThis as { window?: unknown }).window = { WebSocket: Socket };
    vi.resetModules();
  });
  afterEach(() => {
    client?.stop();
    vi.useRealTimers();
    vi.restoreAllMocks();
    if (originalWindow === undefined) delete (globalThis as { window?: unknown }).window;
    else (globalThis as { window?: unknown }).window = originalWindow;
  });

  it('shares authentication, typed delivery, reconnects and cleanup with the browser stream', async () => {
    const callback = vi.fn();
    const onError = vi.fn();
    const { WebSocketCallbackClient } = await import('../events/websocket-client');
    client = new WebSocketCallbackClient({
      host: 'https://agent.test/proxy',
      conversationId: 'conversation',
      apiKey: 'secret +/?',
      callback,
      onError,
    });
    client.start();
    client.start();
    expect(Socket.instances).toHaveLength(1);
    const first = Socket.instances[0];
    expect(first.url).toBe('wss://agent.test/proxy/sockets/events/conversation');
    first.readyState = 1;
    first.onopen?.(new Event('open'));
    expect(first.send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'auth', session_api_key: 'secret +/?' })
    );
    first.onmessage?.({ data: JSON.stringify({ id: 'event-1' }) } as MessageEvent);
    expect(callback).toHaveBeenCalledWith({ id: 'event-1' });
    first.onmessage?.({ data: 'invalid JSON' } as MessageEvent);
    expect(onError).toHaveBeenCalledTimes(1);
    first.onclose?.({ code: 1006, reason: 'connection lost' } as CloseEvent);
    expect(onError).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(1000);
    expect(Socket.instances).toHaveLength(2);
    client.stop();
    expect(Socket.instances[1].close).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });
});
