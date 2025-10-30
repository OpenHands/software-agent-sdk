/**
 * WebSocket client for real-time event streaming
 */

import { Event, ConversationCallbackType } from '../types/base';

// Use native WebSocket in browser, ws library in Node.js
const WebSocketImpl = (() => {
  if (typeof window !== 'undefined' && window.WebSocket) {
    // Browser environment
    return window.WebSocket;
  } else {
    // Node.js environment
    try {
      const ws = require('ws');
      return ws;
    } catch (e) {
      throw new Error('WebSocket implementation not available. Install ws package for Node.js environments.');
    }
  }
})();

export interface WebSocketClientOptions {
  host: string;
  conversationId: string;
  callback: ConversationCallbackType;
  apiKey?: string;
}

export class WebSocketCallbackClient {
  private host: string;
  private conversationId: string;
  private callback: ConversationCallbackType;
  private apiKey?: string;
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
      // Convert HTTP URL to WebSocket URL
      const url = new URL(this.host);
      const wsScheme = url.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${wsScheme}//${url.host}${url.pathname.replace(/\/$/, '')}/sockets/events/${this.conversationId}`;

      // Add API key as query parameter if provided
      const finalUrl = this.apiKey ? `${wsUrl}?session_api_key=${this.apiKey}` : wsUrl;

      this.ws = new WebSocketImpl(finalUrl);

      // Handle events differently for browser vs Node.js
      if (typeof window !== 'undefined') {
        // Browser WebSocket API
        this.ws.onopen = () => {
          console.debug(`WebSocket connected to ${finalUrl}`);
          this.currentDelay = this.reconnectDelay; // Reset delay on successful connection
        };

        this.ws.onmessage = (event: MessageEvent) => {
          try {
            const message = event.data;
            const eventData: Event = JSON.parse(message);
            this.callback(eventData);
          } catch (error) {
            console.error('Error processing WebSocket message:', error);
          }
        };

        this.ws.onclose = (event: CloseEvent) => {
          console.debug(`WebSocket closed: ${event.code} ${event.reason}`);
          this.ws = undefined;

          if (this.shouldReconnect) {
            this.scheduleReconnect();
          }
        };

        this.ws.onerror = (error: Event) => {
          console.debug('WebSocket error:', error);
          if (this.shouldReconnect) {
            this.scheduleReconnect();
          }
        };
      } else {
        // Node.js ws library API
        this.ws.on('open', () => {
          console.debug(`WebSocket connected to ${finalUrl}`);
          this.currentDelay = this.reconnectDelay; // Reset delay on successful connection
        });

        this.ws.on('message', (data: any) => {
          try {
            const message = data.toString();
            const event: Event = JSON.parse(message);
            this.callback(event);
          } catch (error) {
            console.error('Error processing WebSocket message:', error);
          }
        });

        this.ws.on('close', (code: number, reason: any) => {
          console.debug(`WebSocket closed: ${code} ${reason ? reason.toString() : ''}`);
          this.ws = undefined;

          if (this.shouldReconnect) {
            this.scheduleReconnect();
          }
        });

        this.ws.on('error', (error: Error) => {
          console.debug('WebSocket error:', error);
          if (this.shouldReconnect) {
            this.scheduleReconnect();
          }
        });
      }
    } catch (error) {
      console.error('Failed to create WebSocket connection:', error);
      if (this.shouldReconnect) {
        this.scheduleReconnect();
      }
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) {
      return;
    }

    console.debug(`Scheduling WebSocket reconnect in ${this.currentDelay}ms`);

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
