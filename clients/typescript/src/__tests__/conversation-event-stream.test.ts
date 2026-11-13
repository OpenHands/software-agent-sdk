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
  send = vi.fn();
  constructor(readonly url: string) {
    Socket.instances.push(this);
  }
  open() {
    this.readyState = 1;
    this.onopen?.(new Event('open'));
  }
  close = vi.fn(() => this.finish(1000));
  finish(code: number) {
    this.readyState = 3;
    this.onclose?.({ code, reason: '' } as CloseEvent);
  }
  /** Node 22's undici WebSocket: a refused handshake fires `error` and never `close`. */
  refuse() {
    this.readyState = 3;
    this.onerror?.(new Event('error'));
  }
}

/** A socket that never fires `close`, not even when close() is called. */
class ErrorOnlySocket extends Socket {
  close = vi.fn(() => this.refuse());
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
    vi.useFakeTimers();
    vi.spyOn(Math, 'random').mockReturnValue(0);
    globalThis.WebSocket = Socket as unknown as typeof WebSocket;
    Socket.instances = [];
    states = [];
  });
  afterEach(() => {
    stream?.stop();
    globalThis.WebSocket = original;
    vi.useRealTimers();
    vi.restoreAllMocks();
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
    vi.advanceTimersByTime(10_000);
    expect(socket.close).not.toHaveBeenCalled();
  });

  it('retries with exponential backoff, stops at the attempt limit, and resets on open', () => {
    stream = new ConversationEventStream(options());
    stream.start();
    Socket.instances[0].finish(1006);
    vi.advanceTimersByTime(999);
    expect(Socket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    Socket.instances[1].finish(1006);
    vi.advanceTimersByTime(1999);
    expect(Socket.instances).toHaveLength(2);
    vi.advanceTimersByTime(1);
    Socket.instances[2].finish(1006);
    vi.advanceTimersByTime(40_000);
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
    vi.advanceTimersByTime(60_000);
    expect(Socket.instances).toHaveLength(1);
    expect(vi.getTimerCount()).toBe(0);
    stream.start();
    Socket.instances[1].finish(1006);
    stream.stop();
    vi.advanceTimersByTime(60_000);
    expect(Socket.instances).toHaveLength(2);
  });

  it('aborts a stalled handshake and reconnects', () => {
    stream = new ConversationEventStream(options());
    stream.start();
    vi.advanceTimersByTime(10_000);
    expect(Socket.instances[0].close).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1000);
    expect(Socket.instances).toHaveLength(2);
  });

  it('keeps backing off when refused handshakes fire only error, from start() and after a drop', () => {
    globalThis.WebSocket = ErrorOnlySocket as unknown as typeof WebSocket;
    const onError = vi.fn();
    stream = new ConversationEventStream({
      ...options(),
      onError,
      reconnect: { enabled: true },
    });
    stream.start();
    Socket.instances[0].refuse();
    expect(states.at(-1)).toMatchObject({ isReconnecting: true, attemptCount: 1 });
    vi.advanceTimersByTime(1000);
    Socket.instances[1].refuse();
    vi.advanceTimersByTime(2000);
    Socket.instances[2].refuse();
    vi.advanceTimersByTime(4000);
    expect(Socket.instances).toHaveLength(4);
    Socket.instances[3].open();
    expect(states.at(-1)).toMatchObject({ isConnected: true, attemptCount: 0 });

    Socket.instances[3].finish(1012);
    vi.advanceTimersByTime(1000);
    Socket.instances[4].refuse();
    vi.advanceTimersByTime(2000);
    Socket.instances[5].refuse();
    vi.advanceTimersByTime(4000);
    Socket.instances[6].open();
    expect(Socket.instances).toHaveLength(7);
    expect(states.at(-1)).toMatchObject({ isConnected: true, isReconnecting: false });
    expect(onError).toHaveBeenCalledTimes(6);
  });

  it('fails a handshake that never settles after 10 s, even when close() fires no close', () => {
    globalThis.WebSocket = ErrorOnlySocket as unknown as typeof WebSocket;
    stream = new ConversationEventStream(options());
    stream.start();
    vi.advanceTimersByTime(10_000);
    expect(Socket.instances[0].close).toHaveBeenCalledTimes(1);
    expect(states.at(-1)?.error?.message).toMatch('timed out');
    vi.advanceTimersByTime(1000);
    expect(Socket.instances).toHaveLength(2);
    vi.advanceTimersByTime(10_000 + 2000);
    expect(Socket.instances).toHaveLength(3);
  });

  it('schedules once when a refused handshake fires error and then close', () => {
    const onClose = vi.fn();
    const onError = vi.fn();
    stream = new ConversationEventStream({ ...options(), onClose, onError });
    stream.start();
    Socket.instances[0].refuse();
    Socket.instances[0].finish(1006);
    expect(states.at(-1)?.attemptCount).toBe(1);
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(1);
    vi.advanceTimersByTime(1000);
    expect(Socket.instances).toHaveLength(2);
  });

  it('ignores late open, message, error, and close events from replaced sockets', () => {
    const onMessage = vi.fn();
    const onError = vi.fn();
    const onClose = vi.fn();
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
    vi.advanceTimersByTime(20_000);
    expect(Socket.instances).toHaveLength(2);
  });

  it('updates callbacks without reconnecting and uses fresh credentials on retry', () => {
    const onMessage = vi.fn();
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
    expect(vi.getTimerCount()).toBe(0);
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
