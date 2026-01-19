/**
 * Remote events list implementation with caching and synchronization
 */

import { HttpClient } from '../client/http-client';
import { Event, ConversationCallbackType } from '../types/base';
// import { EventSortOrder } from '../types/base'; // Unused for now
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

export class RemoteEventsList {
  private client: HttpClient;
  private conversationId: string;
  private cachedEvents: Event[] = [];
  private cachedEventIds = new Set<string>();
  private lock = new AsyncLock();
  private syncPromise: Promise<void>;

  constructor(client: HttpClient, conversationId: string) {
    this.client = client;
    this.conversationId = conversationId;
    // Perform initial sync
    this.syncPromise = this.doFullSync();
  }

  async ensureSynced(): Promise<void> {
    await this.syncPromise;
  }

  private async doFullSync(): Promise<void> {
    console.debug(`Performing full sync for conversation ${this.conversationId}`);

    const events: Event[] = [];
    let pageId: string | undefined;

    // eslint-disable-next-line no-constant-condition
    while (true) {
      const params: any = { limit: 100 };
      if (pageId) {
        params.page_id = pageId;
      }

      const response = await this.client.get<EventPage>(
        `/api/conversations/${this.conversationId}/events/search`,
        { params }
      );

      const data = response.data;
      events.push(...data.items);

      if (!data.next_page_id) {
        break;
      }
      pageId = data.next_page_id;
    }

    await this.lock.acquire(async () => {
      this.cachedEvents = events;
      this.cachedEventIds.clear();
      events.forEach((e) => this.cachedEventIds.add(e.id));
    });

    console.debug(`Full sync completed, ${events.length} events cached`);
  }

  /**
   * Search events with optional filters.
   * This method queries the server directly and does not use the cache.
   */
  async search(options: EventSearchOptions = {}): Promise<EventPage> {
    const params: any = {
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
    const params: any = {};

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

  async addEvent(event: Event): Promise<void> {
    await this.lock.acquire(async () => {
      // Check if event already exists to avoid duplicates
      if (!this.cachedEventIds.has(event.id)) {
        this.cachedEvents.push(event);
        this.cachedEventIds.add(event.id);
        console.debug(`Added event ${event.id} to local cache`);
      }
    });
  }

  // Alias for compatibility with EventLog interface
  async append(event: Event): Promise<void> {
    await this.addEvent(event);
  }

  createDefaultCallback(): ConversationCallbackType {
    return (event: Event) => {
      this.addEvent(event).catch((error) => {
        console.error('Error adding event to cache:', error);
      });
    };
  }

  async length(): Promise<number> {
    return await this.lock.acquire(async () => this.cachedEvents.length);
  }

  async getEvent(index: number): Promise<Event | undefined> {
    return await this.lock.acquire(async () => this.cachedEvents[index]);
  }

  async getEvents(start?: number, end?: number): Promise<Event[]> {
    await this.ensureSynced();
    return await this.lock.acquire(async () => {
      if (start === undefined && end === undefined) {
        return [...this.cachedEvents];
      }
      return this.cachedEvents.slice(start, end);
    });
  }

  async *[Symbol.asyncIterator](): AsyncIterableIterator<Event> {
    const events = await this.getEvents();
    for (const event of events) {
      yield event;
    }
  }
}

// Simple async lock implementation
class AsyncLock {
  private locked = false;
  private queue: Array<() => void> = [];

  async acquire<T>(fn: () => Promise<T> | T): Promise<T> {
    return new Promise((resolve, reject) => {
      const execute = async () => {
        try {
          const result = await fn();
          resolve(result);
        } catch (error) {
          reject(error);
        } finally {
          this.locked = false;
          const next = this.queue.shift();
          if (next) {
            next();
          }
        }
      };

      if (this.locked) {
        this.queue.push(execute);
      } else {
        this.locked = true;
        execute();
      }
    });
  }
}
