/**
 * WebSocket client for real-time event streaming
 */

import { Event, ConversationCallbackType } from '../types/base';

// Every runtime this package supports supplies a standards-compatible
// WebSocket global: browsers, and Node.js since 22.4.
//
// IMPORTANT: this must never throw. It runs whenever this file is imported,
// and this file is transitively imported by the package barrel (via
// RemoteConversation), so any throw here crashes consumers that merely
// `import { RemoteWorkspace } from "@openhands/typescript-client"` even when
// they have no intent to open a WebSocket. Reading a missing global yields
// undefined rather than throwing, and the "no implementation available"
// condition stays deferred to connect() time, where it is surfaced through
// the existing onError callback channel.
const WebSocketImpl: typeof WebSocket | undefined = globalThis.WebSocket;

/**
 * Error callback type for reporting non-fatal errors.
 * Library code calls this instead of console.error so callers can handle errors.
 */
export type ErrorCallbackType = (error: Error) => void;

export interface WebSocketClientOptions {
  host: string;
  conversationId: string;
  callback: ConversationCallbackType;
  apiKey?: string;
  /** Optional error callback. Called for non-fatal errors (parse failures, connection issues). */
  onError?: ErrorCallbackType;
}

export class WebSocketCallbackClient {
  private host: string;
  private conversationId: string;
  private callback: ConversationCallbackType;
  private apiKey?: string;
  private onError?: ErrorCallbackType;
  private ws?: any; // WebSocket instance (browser or Node.js)
  private reconnectDelay = 1000;
  private maxReconnectDelay = 30000;
  private currentDelay = 1000;
  private shouldReconnect = true;
  private reconnectTimer?: NodeJS.Timeout;

  constructor(options: WebSocketClientOptions) {
    this.host = options.host;
    this.conversationId = options.conversationId;
    this.callback = options.callback;
    this.apiKey = options.apiKey;
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
      // Convert HTTP URL to WebSocket URL
      const url = new URL(this.host);
      const wsScheme = url.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${wsScheme}//${url.host}${url.pathname.replace(/\/$/, '')}/sockets/events/${this.conversationId}`;

      // Add API key as query parameter if provided
      const finalUrl = this.apiKey ? `${wsUrl}?session_api_key=${this.apiKey}` : wsUrl;

      this.ws = new WebSocketImpl(finalUrl);

      this.ws.onopen = () => {
        this.currentDelay = this.reconnectDelay;
      };

      this.ws.onmessage = (event: { data: any }) => {
        try {
          const message = typeof event.data === 'string' ? event.data : event.data.toString();
          const eventData: Event = JSON.parse(message);
          this.callback(eventData);
        } catch (error) {
          this.reportError(
            new Error(
              `Error processing WebSocket message: ${error instanceof Error ? error.message : String(error)}`
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
          `Failed to create WebSocket connection: ${error instanceof Error ? error.message : String(error)}`
        )
      );
      if (this.shouldReconnect) {
        this.scheduleReconnect();
      }
    }
  }

  /**
   * Report a non-fatal error via the onError callback if provided.
   */
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
        // Exponential backoff with jitter
        this.currentDelay = Math.min(this.currentDelay * 2, this.maxReconnectDelay);
      }
    }, this.currentDelay);
  }
}
