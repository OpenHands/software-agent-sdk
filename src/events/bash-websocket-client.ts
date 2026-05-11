/**
 * WebSocket client for bash event streaming.
 */

import { BashEvent } from '../models/workspace';
import { ErrorCallbackType } from './websocket-client';

// IMPORTANT: this block must never throw. See the matching note in
// `events/websocket-client.ts` — the "no WebSocket implementation"
// condition is deferred to connect() time so importing this module does
// not crash consumers that never use bash event streaming.
let WebSocketImpl: any;

if (typeof window !== 'undefined' && window.WebSocket) {
  WebSocketImpl = window.WebSocket;
} else {
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const ws = require('ws');
    WebSocketImpl = ws;
  } catch {
    WebSocketImpl = undefined;
  }
}

export interface BashWebSocketClientOptions {
  host: string;
  callback: (event: BashEvent) => void;
  apiKey?: string;
  resendMode?: 'all';
  onError?: ErrorCallbackType;
}

export class BashWebSocketClient {
  private host: string;
  private callback: (event: BashEvent) => void;
  private apiKey?: string;
  private resendMode?: 'all';
  private onError?: ErrorCallbackType;
  private ws?: any;
  private reconnectDelay = 1000;
  private maxReconnectDelay = 30000;
  private currentDelay = 1000;
  private shouldReconnect = true;
  private reconnectTimer?: NodeJS.Timeout;

  constructor(options: BashWebSocketClientOptions) {
    this.host = options.host;
    this.callback = options.callback;
    this.apiKey = options.apiKey;
    this.resendMode = options.resendMode;
    this.onError = options.onError;
  }

  start(): void {
    if (this.ws) {
      return;
    }

    this.shouldReconnect = true;
    this.connect();
  }

  stop(): void {
    this.shouldReconnect = false;

    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = undefined;
    }

    if (this.ws) {
      this.ws.close();
      this.ws = undefined;
    }
  }

  private connect(): void {
    try {
      if (!WebSocketImpl) {
        throw new Error(
          'WebSocket implementation not available. Install the `ws` package, ' +
            'or run in an environment with a global WebSocket constructor.'
        );
      }
      const url = new URL(this.host);
      const wsScheme = url.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${wsScheme}//${url.host}${url.pathname.replace(/\/$/, '')}/sockets/bash-events`;
      const query = new URLSearchParams();

      if (this.apiKey) {
        query.set('session_api_key', this.apiKey);
      }

      if (this.resendMode) {
        query.set('resend_mode', this.resendMode);
      }

      const finalUrl = query.size > 0 ? `${wsUrl}?${query.toString()}` : wsUrl;
      this.ws = new WebSocketImpl(finalUrl);

      this.ws.onopen = () => {
        this.currentDelay = this.reconnectDelay;
      };

      this.ws.onmessage = (event: { data: unknown }) => {
        try {
          const message = typeof event.data === 'string' ? event.data : String(event.data);
          const eventData: BashEvent = JSON.parse(message);
          this.callback(eventData);
        } catch (error) {
          this.reportError(
            new Error(
              `Error processing bash WebSocket message: ${error instanceof Error ? error.message : String(error)}`
            )
          );
        }
      };

      this.ws.onclose = () => {
        this.ws = undefined;
        if (this.shouldReconnect) {
          this.scheduleReconnect();
        }
      };

      this.ws.onerror = () => {
        if (this.shouldReconnect) {
          this.scheduleReconnect();
        }
      };
    } catch (error) {
      this.reportError(
        new Error(
          `Failed to create bash WebSocket connection: ${error instanceof Error ? error.message : String(error)}`
        )
      );
      if (this.shouldReconnect) {
        this.scheduleReconnect();
      }
    }
  }

  private reportError(error: Error): void {
    if (this.onError) {
      this.onError(error);
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) {
      return;
    }

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = undefined;
      if (this.shouldReconnect) {
        this.connect();
        this.currentDelay = Math.min(this.currentDelay * 2, this.maxReconnectDelay);
      }
    }, this.currentDelay);
  }
}
