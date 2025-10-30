/**
 * Remote events list implementation with caching and synchronization
 */

import { HttpClient } from '../client/http-client';
import { Event, ConversationCallbackType } from '../types/base';
// import { EventSortOrder } from '../types/base'; // Unused for now
import { EventPage } from '../types/base';

export class RemoteEventsList {
  private client: HttpClient;
  private conversationId: string;
  private cachedEvents: Event[] = [];
  private cachedEventIds = new Set<string>();
  private lock = new AsyncLock();

  constructor(client: HttpClient, conversationId: string) {
    this.client = client;
    this.conversationId = conversationId;
    // Perform initial sync
    this.doFullSync();
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
