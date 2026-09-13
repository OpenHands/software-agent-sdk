import type { WebSocketCallbackClient } from '../events/websocket-client';

class Socket {
  static instances: Socket[] = [];
  readyState = 0;
  onopen?: (event: Event) => void;
  onclose?: (event: CloseEvent) => void;
  onmessage?: (event: MessageEvent) => void;
  send = jest.fn();
  close = jest.fn();
  constructor(readonly url: string) {
    Socket.instances.push(this);
  }
}

describe('typed conversation event transport', () => {
  let client: WebSocketCallbackClient;
  beforeEach(() => {
    jest.useFakeTimers();
    jest.spyOn(Math, 'random').mockReturnValue(0);
    Socket.instances = [];
    jest.doMock('ws', () => Socket);
  });
  afterEach(() => {
    client?.stop();
    jest.useRealTimers();
    jest.restoreAllMocks();
    jest.dontMock('ws');
  });

  it('shares authentication, typed delivery, reconnects and cleanup with the browser stream', () => {
    const callback = jest.fn();
    const onError = jest.fn();
    jest.isolateModules(() => {
      const { WebSocketCallbackClient } = jest.requireActual('../events/websocket-client');
      client = new WebSocketCallbackClient({
        host: 'https://agent.test/proxy',
        conversationId: 'conversation',
        apiKey: 'secret +/?',
        callback,
        onError,
      });
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
    jest.advanceTimersByTime(1000);
    expect(Socket.instances).toHaveLength(2);
    client.stop();
    expect(Socket.instances[1].close).toHaveBeenCalledTimes(1);
    expect(jest.getTimerCount()).toBe(0);
  });
});
