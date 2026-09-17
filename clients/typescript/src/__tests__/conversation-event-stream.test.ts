import {
  ConversationEventStream,
  buildConversationEventStreamUrl,
  type ConversationEventStreamState,
} from '../client/conversation-event-stream';

class Socket {
  static instances: Socket[] = [];
  readyState = 0;
  onopen: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  send = jest.fn();
  constructor(readonly url: string) {
    Socket.instances.push(this);
  }
  open() {
    this.readyState = 1;
    this.onopen?.(new Event('open'));
  }
  close = jest.fn(() => this.finish(1000));
  finish(code: number) {
    this.readyState = 3;
    this.onclose?.({ code, reason: '' } as CloseEvent);
  }
}

describe('ConversationEventStream', () => {
  const original = globalThis.WebSocket;
  let stream: ConversationEventStream;
  let states: ConversationEventStreamState[];
  const options = () => ({
    url: buildConversationEventStreamUrl('https://agent.test/runtime/42', 'conv-1'),
    onStateChange: (state: ConversationEventStreamState) => states.push(state),
    reconnect: { enabled: true, maxAttempts: 2 },
  });
  beforeEach(() => {
    jest.useFakeTimers();
    jest.spyOn(Math, 'random').mockReturnValue(0);
    globalThis.WebSocket = Socket as unknown as typeof WebSocket;
    Socket.instances = [];
    states = [];
  });
  afterEach(() => {
    stream?.stop();
    globalThis.WebSocket = original;
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  it('preserves proxy paths and authenticates before consumer messages, without URL secrets', () => {
    stream = new ConversationEventStream({
      ...options(),
      sessionApiKey: 'secret +/=?',
      queryParams: { resend_mode: 'since', after_timestamp: '2026-09-12T00:00:00+00:00' },
      onOpen: () => stream.send('message'),
    });
    stream.start();
    const socket = Socket.instances[0];
    const url = new URL(socket.url);
    expect(url.pathname).toBe('/runtime/42/sockets/events/conv-1');
    expect(url.protocol).toBe('wss:');
    expect(url.searchParams.get('after_timestamp')).toBe('2026-09-12T00:00:00+00:00');
    expect(url.searchParams.has('session_api_key')).toBe(false);
    socket.open();
    expect(socket.send.mock.calls).toEqual([
      [JSON.stringify({ type: 'auth', session_api_key: 'secret +/=?' })],
      ['message'],
    ]);
    expect(states.at(-1)?.isConnected).toBe(true);
    jest.advanceTimersByTime(10_000);
    expect(socket.close).not.toHaveBeenCalled();
  });

  it('retries with exponential backoff, stops at the attempt limit, and resets on open', () => {
    stream = new ConversationEventStream(options());
    stream.start();
    Socket.instances[0].finish(1006);
    jest.advanceTimersByTime(999);
    expect(Socket.instances).toHaveLength(1);
    jest.advanceTimersByTime(1);
    Socket.instances[1].finish(1006);
    jest.advanceTimersByTime(1999);
    expect(Socket.instances).toHaveLength(2);
    jest.advanceTimersByTime(1);
    Socket.instances[2].finish(1006);
    jest.advanceTimersByTime(40_000);
    expect(Socket.instances).toHaveLength(3);
    expect(states.at(-1)?.isReconnecting).toBe(false);
    stream.reconnect();
    Socket.instances[3].open();
    expect(states.at(-1)?.attemptCount).toBe(0);
  });

  it('cancels pending reconnects and handshakes on stop', () => {
    stream = new ConversationEventStream(options());
    stream.start();
    stream.stop();
    jest.advanceTimersByTime(60_000);
    expect(Socket.instances).toHaveLength(1);
    expect(jest.getTimerCount()).toBe(0);
    stream.start();
    Socket.instances[1].finish(1006);
    stream.stop();
    jest.advanceTimersByTime(60_000);
    expect(Socket.instances).toHaveLength(2);
  });

  it('aborts a stalled handshake and reconnects', () => {
    stream = new ConversationEventStream(options());
    stream.start();
    jest.advanceTimersByTime(10_000);
    expect(Socket.instances[0].close).toHaveBeenCalledTimes(1);
    jest.advanceTimersByTime(1000);
    expect(Socket.instances).toHaveLength(2);
  });

  it('ignores late open, message, error, and close events from replaced sockets', () => {
    const onMessage = jest.fn();
    const onError = jest.fn();
    const onClose = jest.fn();
    stream = new ConversationEventStream({ ...options(), onMessage, onError, onClose });
    stream.start();
    const old = Socket.instances[0];
    const callbacks = [old.onopen, old.onmessage, old.onerror, old.onclose];
    stream.reconnect();
    Socket.instances[1].open();
    callbacks.forEach((callback) => callback?.({ code: 1006 } as never));
    expect(stream.readyState).toBe(1);
    expect(states.at(-1)?.isConnected).toBe(true);
    expect(onMessage).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    jest.advanceTimersByTime(20_000);
    expect(Socket.instances).toHaveLength(2);
  });

  it('updates callbacks without reconnecting and uses fresh credentials on retry', () => {
    const onMessage = jest.fn();
    stream = new ConversationEventStream(options());
    stream.start();
    stream.updateOptions({ ...options(), onMessage, sessionApiKey: 'updated' });
    Socket.instances[0].open();
    const message = { data: 'one' } as MessageEvent;
    Socket.instances[0].onmessage?.(message);
    expect(onMessage).toHaveBeenCalledWith(message);
    expect(Socket.instances).toHaveLength(1);
    expect(Socket.instances[0].send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'auth', session_api_key: 'updated' })
    );
  });

  it('reports missing WebSocket only at start and rejects sends while disconnected', () => {
    globalThis.WebSocket = undefined as unknown as typeof WebSocket;
    stream = new ConversationEventStream({ ...options(), reconnect: { enabled: false } });
    expect(() => stream.start()).not.toThrow();
    expect(states.at(-1)?.error).toBeInstanceOf(Error);
    expect(() => stream.send('message')).toThrow('not open');
    expect(jest.getTimerCount()).toBe(0);
  });

  it.each([
    { url: 'wss://agent.test/events?session_api_key=secret' },
    { queryParams: { session_api_key: 'secret' } },
    { url: 'wss://user:secret@agent.test/events' },
  ])('rejects credentials in URLs or replay parameters: %j', (override) => {
    stream = new ConversationEventStream({
      ...options(),
      ...override,
      reconnect: { enabled: false },
    });
    stream.start();
    expect(Socket.instances).toHaveLength(0);
    expect(states.at(-1)?.error?.message).toMatch('first-frame authentication');
  });
});
