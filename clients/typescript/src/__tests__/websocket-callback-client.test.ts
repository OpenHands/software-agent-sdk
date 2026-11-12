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
  let client: WebSocketCallbackClient;
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(Math, 'random').mockReturnValue(0);
    Socket.instances = [];
    // Browsers and Node 22+ both expose the constructor as globalThis.WebSocket
    // (there is no `window` in Node).
    vi.stubGlobal('WebSocket', Socket);
    vi.resetModules();
  });
  afterEach(() => {
    client?.stop();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
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

  it('uses a global WebSocket installed after the module was imported', async () => {
    vi.stubGlobal('WebSocket', undefined);
    const onError = vi.fn();
    const { WebSocketCallbackClient } = await import('../events/websocket-client');
    vi.stubGlobal('WebSocket', Socket);
    client = new WebSocketCallbackClient({
      host: 'http://agent.test',
      conversationId: 'conversation',
      callback: vi.fn(),
      onError,
    });
    client.start();
    expect(Socket.instances.map((socket) => socket.url)).toEqual([
      'ws://agent.test/sockets/events/conversation',
    ]);
    expect(onError).not.toHaveBeenCalled();
  });

  it('streams bash events over the global WebSocket', async () => {
    const callback = vi.fn();
    const onError = vi.fn();
    const { BashWebSocketClient } = await import('../events/bash-websocket-client');
    const bash = new BashWebSocketClient({
      host: 'https://agent.test/proxy/',
      apiKey: 'secret',
      resendMode: 'all',
      callback,
      onError,
    });
    bash.start();
    expect(Socket.instances).toHaveLength(1);
    expect(Socket.instances[0].url).toBe(
      'wss://agent.test/proxy/sockets/bash-events?session_api_key=secret&resend_mode=all'
    );
    Socket.instances[0].onmessage?.({
      data: JSON.stringify({ kind: 'BashOutput', stdout: 'hi' }),
    } as MessageEvent);
    expect(callback).toHaveBeenCalledWith({ kind: 'BashOutput', stdout: 'hi' });
    bash.stop();
    expect(Socket.instances[0].close).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled();
  });
});
