/** Browser transport for the Agent Server conversation event socket. */
export interface ConversationEventStreamOptions {
  /** Full event socket URL, including any reverse-proxy prefix. */
  url: string;
  queryParams?: Record<string, string | boolean>;
  sessionApiKey?: string | null;
  onOpen?: (event: Event) => void;
  onClose?: (event: CloseEvent) => void;
  onMessage?: (event: MessageEvent) => void;
  onError?: (event: Event) => void;
  onStateChange?: (state: ConversationEventStreamState) => void;
  reconnect?: { enabled?: boolean; maxAttempts?: number };
}

export interface ConversationEventStreamState {
  isConnected: boolean;
  isReconnecting: boolean;
  attemptCount: number;
  error: Error | null;
}

/** Resolve the canonical endpoint without discarding a deployment's path prefix. */
export function buildConversationEventStreamUrl(host: string, conversationId: string): string {
  const url = new URL(host);
  if (!['http:', 'https:', 'ws:', 'wss:'].includes(url.protocol)) {
    throw new Error('Unsupported Agent Server URL protocol');
  }
  if (!conversationId.trim()) throw new Error('A conversation ID is required');
  url.protocol = ['https:', 'wss:'].includes(url.protocol) ? 'wss:' : 'ws:';
  url.pathname = `${url.pathname.replace(/\/$/, '')}/sockets/events/${encodeURIComponent(conversationId)}`;
  url.search = '';
  url.hash = '';
  return url.toString();
}

/**
 * Owns authentication, reconnects and handshake cleanup. Callbacks receive each
 * frame once; no message history is retained. Call stop() when the owner unmounts.
 * A Web-standard global WebSocket is required only when start() is called.
 */
export class ConversationEventStream {
  private socket: WebSocket | null = null;
  private active = false;
  private retryTimer?: ReturnType<typeof setTimeout>;
  private handshakeTimer?: ReturnType<typeof setTimeout>;
  private state: ConversationEventStreamState = {
    isConnected: false,
    isReconnecting: false,
    attemptCount: 0,
    error: null,
  };

  constructor(private options: ConversationEventStreamOptions) {}

  /** Update callbacks and credentials for subsequent handshakes without reconnecting. */
  updateOptions(options: ConversationEventStreamOptions): void {
    this.options = options;
  }

  get readyState(): number {
    return this.socket?.readyState ?? 3;
  }

  start(): void {
    if (this.active) return;
    this.active = true;
    this.update({ attemptCount: 0 });
    this.connect();
  }

  stop(): void {
    this.active = false;
    this.clearTimers();
    const socket = this.socket;
    this.socket = null;
    this.update({ isConnected: false, isReconnecting: false });
    socket?.close();
  }

  reconnect(): void {
    this.active = true;
    this.clearTimers();
    const socket = this.socket;
    this.socket = null;
    // Late events from a replaced socket must not affect the new connection.
    if (socket) {
      socket.onopen = socket.onclose = socket.onerror = socket.onmessage = null;
      socket.close();
    }
    this.update({ isConnected: false, isReconnecting: true, attemptCount: 0, error: null });
    this.connect();
  }

  send(data: string | Blob | BufferSource): void {
    if (this.socket?.readyState !== 1) throw new Error('Conversation event stream is not open');
    this.socket.send(data);
  }

  private update(patch: Partial<ConversationEventStreamState>): void {
    this.state = { ...this.state, ...patch };
    this.options.onStateChange?.({ ...this.state });
  }

  private clearTimers(): void {
    clearTimeout(this.retryTimer);
    clearTimeout(this.handshakeTimer);
    this.retryTimer = this.handshakeTimer = undefined;
  }

  private connect(): void {
    if (!this.active) return;
    try {
      const url = new URL(this.options.url);
      for (const [key, value] of Object.entries(this.options.queryParams ?? {})) {
        url.searchParams.set(key, String(value));
      }
      if (url.username || url.password || url.searchParams.has('session_api_key')) {
        throw new Error('Use sessionApiKey for first-frame authentication, not URL credentials');
      }
      const socket = new WebSocket(url.toString());
      this.socket = socket;
      this.handshakeTimer = setTimeout(() => {
        if (this.socket === socket && socket.readyState === 0) socket.close();
      }, 10_000);
      socket.onopen = (event) => {
        if (this.socket !== socket || !this.active) return;
        clearTimeout(this.handshakeTimer);
        const key = this.options.sessionApiKey;
        if (key) socket.send(JSON.stringify({ type: 'auth', session_api_key: key }));
        this.update({ isConnected: true, isReconnecting: false, attemptCount: 0, error: null });
        this.options.onOpen?.(event);
      };
      socket.onmessage = (event) => {
        if (this.socket === socket && this.active) this.options.onMessage?.(event);
      };
      socket.onerror = (event) => {
        if (this.socket !== socket || !this.active) return;
        this.update({ isConnected: false });
        this.options.onError?.(event);
      };
      socket.onclose = (event) => {
        if (this.socket !== socket) {
          if (!this.active && this.socket === null) this.options.onClose?.(event);
          return;
        }
        clearTimeout(this.handshakeTimer);
        this.socket = null;
        this.update({
          isConnected: false,
          ...(event.code !== 1000
            ? {
                error: new Error(
                  `WebSocket closed with code ${event.code}: ${event.reason || 'Connection closed unexpectedly'}`
                ),
              }
            : {}),
        });
        if (event.code !== 1000) this.options.onError?.(event);
        this.options.onClose?.(event);
        this.scheduleReconnect();
      };
    } catch (error) {
      this.update({
        isConnected: false,
        error: error instanceof Error ? error : new Error(String(error)),
      });
      this.options.onError?.(new Event('error'));
      this.scheduleReconnect();
    }
  }

  private scheduleReconnect(): void {
    if (
      !this.active ||
      !this.options.reconnect?.enabled ||
      this.state.attemptCount >= (this.options.reconnect.maxAttempts ?? Infinity)
    ) {
      this.update({ isReconnecting: false });
      return;
    }
    const attemptCount = this.state.attemptCount + 1;
    this.update({ isReconnecting: true, attemptCount });
    const delay = Math.min(1_000 * 2 ** (attemptCount - 1), 30_000);
    this.retryTimer = setTimeout(
      () => {
        this.retryTimer = undefined;
        this.connect();
      },
      delay + Math.random() * delay * 0.3
    );
  }
}
