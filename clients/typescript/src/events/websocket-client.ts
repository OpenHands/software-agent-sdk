/**
 * WebSocket client for real-time event streaming
 */

import { Event, ConversationCallbackType } from '../types/base';
import {
  ConversationEventStream,
  buildConversationEventStreamUrl,
} from '../client/conversation-event-stream';

// Use native WebSocket in browser, ws library in Node.js.
//
// IMPORTANT: this block must never throw. It runs whenever this file is
// imported, and this file is transitively imported by the package barrel
// (via RemoteConversation), so any throw here crashes consumers that
// merely `import { RemoteWorkspace } from "@openhands/typescript-client"`
// even when they have no intent to open a WebSocket. The "no implementation
// available" condition is deferred to connect() time, where it is surfaced
// through the existing onError callback channel.
let WebSocketImpl: any;

if (typeof window !== 'undefined' && window.WebSocket) {
  // Browser environment
  WebSocketImpl = window.WebSocket;
} else {
  // Node.js environment
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const ws = require('ws');
    WebSocketImpl = ws;
  } catch {
    // Leave WebSocketImpl undefined; connect() reports the error via onError.
    WebSocketImpl = undefined;
  }
}

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

/** Typed event adapter over the same transport used by browser consumers. */
export class WebSocketCallbackClient {
  private stream?: ConversationEventStream;

  constructor(private options: WebSocketClientOptions) {}

  private createStream(): ConversationEventStream {
    const options = this.options;
    let lastError: Error | null = null;
    return new ConversationEventStream({
      url: buildConversationEventStreamUrl(options.host, options.conversationId),
      sessionApiKey: options.apiKey,
      reconnect: { enabled: true },
      createWebSocket: (url) => {
        if (!WebSocketImpl) {
          throw new Error(
            'WebSocket implementation not available. Install the `ws` package, ' +
              'or run in an environment with a global WebSocket constructor.'
          );
        }
        return new WebSocketImpl(url);
      },
      onMessage: (event) => {
        try {
          const message = typeof event.data === 'string' ? event.data : event.data.toString();
          options.callback(JSON.parse(message) as Event);
        } catch (error) {
          options.onError?.(
            new Error(
              `Error processing WebSocket message: ${error instanceof Error ? error.message : String(error)}`
            )
          );
        }
      },
      onStateChange: ({ error }) => {
        if (error && error !== lastError) options.onError?.(error);
        lastError = error;
      },
    });
  }

  start(): void {
    try {
      this.stream ??= this.createStream();
      this.stream.start();
    } catch (error) {
      this.options.onError?.(error instanceof Error ? error : new Error(String(error)));
    }
  }

  stop(): void {
    this.stream?.stop();
  }
}
