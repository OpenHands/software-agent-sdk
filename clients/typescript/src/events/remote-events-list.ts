/**
 * Remote events list — thin wrapper around the events search API.
 *
 * Events received via WebSocket are kept in a local cache so callers can
 * iterate them without extra network calls. For historical events use
 * `search()` or `getEvents()` which hit the server on demand.
 */

import { HttpClient, HttpError } from '../client/http-client';
import type { HttpClientOptions } from '../client/http-client';
import { Event, ConversationCallbackType } from '../types/base';
import { EventPage } from '../types/base';

/**
 * Options for searching events
 */
export interface EventSearchOptions {
  /** Maximum number of events to return per page */
  limit?: number;
  /** Page ID for pagination */
  page_id?: string;
  /** Filter by event kind/type (e.g., ActionEvent, MessageEvent) */
  kind?: string;
  /** Filter by event source (e.g., agent, user, environment) */
  source?: string;
  /** Filter by message content (case-insensitive) */
  body?: string;
  /** Sort order for events */
  sort_order?: 'TIMESTAMP' | 'TIMESTAMP_DESC';
  /** Filter: event timestamp >= this datetime (ISO 8601 format) */
  timestamp__gte?: string;
  /** Filter: event timestamp < this datetime (ISO 8601 format) */
  timestamp__lt?: string;
}

export type RemoteEventsListOptions = HttpClientOptions;

export interface GetEventsOptions {
  start?: number;
  end?: number;
  /** Maximum size of the fetched/merged prefix, before applying start/end. */
  maxEvents?: number;
  signal?: AbortSignal;
}

export class RemoteEventsList {
  private client: HttpClient;
  private conversationId: string;
  private cachedEvents: Event[] = [];
  private cachedEventIds = new Set<string>();

  constructor(clientOrOptions: HttpClient | RemoteEventsListOptions, conversationId: string) {
    this.client =
      clientOrOptions instanceof HttpClient ? clientOrOptions : new HttpClient(clientOrOptions);
    this.conversationId = conversationId;
  }

  /**
   * Search events with optional filters.
   * Queries the server directly.
   */
  async search(options: EventSearchOptions = {}): Promise<EventPage> {
    const params: Record<string, unknown> = {
      limit: options.limit ?? 100,
    };

    if (options.page_id) params.page_id = options.page_id;
    if (options.kind) params.kind = options.kind;
    if (options.source) params.source = options.source;
    if (options.body) params.body = options.body;
    if (options.sort_order) params.sort_order = options.sort_order;
    if (options.timestamp__gte) params.timestamp__gte = options.timestamp__gte;
    if (options.timestamp__lt) params.timestamp__lt = options.timestamp__lt;

    const response = await this.client.get<EventPage>(
      `/api/conversations/${this.conversationId}/events/search`,
      { params }
    );

    return response.data;
  }

  /**
   * Count events matching the given filters.
   */
  async count(
    options: Omit<EventSearchOptions, 'limit' | 'page_id' | 'sort_order'> = {}
  ): Promise<number> {
    const params: Record<string, unknown> = {};

    if (options.kind) params.kind = options.kind;
    if (options.source) params.source = options.source;
    if (options.body) params.body = options.body;
    if (options.timestamp__gte) params.timestamp__gte = options.timestamp__gte;
    if (options.timestamp__lt) params.timestamp__lt = options.timestamp__lt;

    const response = await this.client.get<number>(
      `/api/conversations/${this.conversationId}/events/count`,
      { params }
    );

    return response.data;
  }

  /**
   * Get a server event by ID.
   */
  async getEventById(eventId: string): Promise<Event> {
    const response = await this.client.get<Event>(
      `/api/conversations/${this.conversationId}/events/${eventId}`
    );
    return response.data;
  }

  /**
   * Batch get server events by ID.
   */
  async getEventsById(eventIds: string[]): Promise<Array<Event | null>> {
    return Promise.all(
      eventIds.map(async (eventId) => {
        try {
          return await this.getEventById(eventId);
        } catch (error) {
          if (error instanceof HttpError && error.status === 404) {
            return null;
          }
          throw error;
        }
      })
    );
  }

  async addEvent(event: Event): Promise<void> {
    if (!this.cachedEventIds.has(event.id)) {
      this.cachedEvents.push(event);
      this.cachedEventIds.add(event.id);
    }
  }

  // Alias for compatibility with EventLog interface
  async append(event: Event): Promise<void> {
    await this.addEvent(event);
  }

  createDefaultCallback(onError?: (error: Error) => void): ConversationCallbackType {
    return (event: Event) => {
      this.addEvent(event).catch((error) => {
        if (onError) {
          onError(
            error instanceof Error ? error : new Error(`Error adding event to cache: ${error}`)
          );
        }
      });
    };
  }

  async length(): Promise<number> {
    return this.cachedEvents.length;
  }

  async getEvent(index: number): Promise<Event | undefined> {
    return this.cachedEvents[index];
  }

  /**
   * Fetch all events from the server, merged with any locally cached
   * events received via WebSocket. A maxEvents budget bounds that prefix before
   * applying start/end slicing. Without a budget, fetches the entire history.
   */
  async getEvents(options?: GetEventsOptions): Promise<Event[]>;
  async getEvents(start?: number, end?: number): Promise<Event[]>;
  async getEvents(startOrOptions?: number | GetEventsOptions, end?: number): Promise<Event[]> {
    const options =
      typeof startOrOptions === 'object' ? startOrOptions : { start: startOrOptions, end };
    const { start, maxEvents, signal } = options;
    if (maxEvents !== undefined && (!Number.isSafeInteger(maxEvents) || maxEvents < 0)) {
      throw new RangeError('maxEvents must be a non-negative safe integer');
    }
    signal?.throwIfAborted();
    if (maxEvents === 0) return [];

    const budget = maxEvents ?? Infinity;
    const remote: Event[] = [];
    let pageId: string | undefined;

    while (remote.length < budget) {
      signal?.throwIfAborted();
      const limit = Math.min(100, budget - remote.length);
      const params: Record<string, unknown> = { limit };
      if (pageId) params.page_id = pageId;

      const response = await this.client.get<EventPage>(
        `/api/conversations/${this.conversationId}/events/search`,
        { params, signal }
      );

      signal?.throwIfAborted();
      const data = response.data;
      remote.push(...data.items.slice(0, limit));

      if (!data.next_page_id) break;
      pageId = data.next_page_id;
    }

    const remoteIds = new Set(remote.map((event) => event.id));
    const merged = [...remote];
    for (const event of this.cachedEvents) {
      if (merged.length >= budget) break;
      if (!remoteIds.has(event.id)) merged.push(event);
    }
    return merged.slice(start, options.end);
  }

  async *[Symbol.asyncIterator](): AsyncIterableIterator<Event> {
    const events = await this.getEvents();
    for (const event of events) {
      yield event;
    }
  }
}
