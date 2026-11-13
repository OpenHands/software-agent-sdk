import WebSocket from 'ws';

import type { BashWebSocketClient } from '../events/bash-websocket-client';

class Socket {
  static instances: Socket[] = [];
  onopen?: (() => void) | null;
  onclose?: (() => void) | null;
  onerror?: (() => void) | null;
  onmessage?: ((event: { data: string }) => void) | null;
  close = vi.fn();
  constructor(readonly url: string) {
    Socket.instances.push(this);
  }
}

describe('bash event socket cleanup', () => {
  const originalWindow = (globalThis as { window?: unknown }).window;
  let client: BashWebSocketClient;

  beforeEach(() => {
    vi.useFakeTimers();
    Socket.instances = [];
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

  it('keeps a replacement connected when the stopped socket closes late', async () => {
    const { BashWebSocketClient } = await import('../events/bash-websocket-client');
    client = new BashWebSocketClient({ host: 'https://agent.test', callback: vi.fn() });
    client.start();
    const first = Socket.instances[0];
    first.onopen?.();

    client.stop();
    client.start();
    first.onerror?.();
    first.onclose?.();
    client.start();

    expect(first.close).toHaveBeenCalledTimes(1);
    expect(Socket.instances).toHaveLength(2);
    expect(vi.getTimerCount()).toBe(0);

    Socket.instances[1].onclose?.();
    vi.advanceTimersByTime(1000);
    expect(Socket.instances).toHaveLength(3);
  });

  it('does not deliver late messages or schedule retries after stop', async () => {
    const { BashWebSocketClient } = await import('../events/bash-websocket-client');
    const callback = vi.fn();
    client = new BashWebSocketClient({ host: 'https://agent.test', callback });
    client.start();
    const socket = Socket.instances[0];

    client.stop();
    socket.onmessage?.({ data: JSON.stringify({ id: 'late-event' }) });
    socket.onerror?.();
    socket.onclose?.();

    expect(callback).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('safely stops a connecting Node ws socket', async () => {
    vi.useRealTimers();
    const sockets: WebSocket[] = [];
    class ConnectingSocket extends WebSocket {
      constructor(url: string) {
        super(url);
        sockets.push(this);
      }
    }
    (globalThis as { window?: unknown }).window = { WebSocket: ConnectingSocket };
    const { BashWebSocketClient } = await import('../events/bash-websocket-client');
    client = new BashWebSocketClient({ host: 'http://127.0.0.1:1', callback: vi.fn() });
    client.start();
    const socket = sockets[0];
    if (!socket) throw new Error('The real ws constructor was not used');
    expect(socket.readyState).toBe(WebSocket.CONNECTING);
    const closed = new Promise<void>((resolve) => socket.once('close', () => resolve()));

    client.stop();

    await closed;
    expect(socket.readyState).toBe(WebSocket.CLOSED);
  });
});
