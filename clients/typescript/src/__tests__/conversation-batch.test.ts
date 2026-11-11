import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { ConversationClient } from '../client/conversation-client';
import { HttpError } from '../client/http-client';
import { ConversationManager } from '../conversation/conversation-manager';

// Exercise real HTTP serialization and error handling against the server's batch contract.
describe.each([
  ['ConversationClient', ConversationClient],
  ['ConversationManager', ConversationManager],
] as const)('%s conversation batches', (_name, Client) => {
  let server: Server;
  let host: string;
  let batches: string[][];
  let rejectBatch: number | undefined;

  beforeEach(async () => {
    batches = [];
    rejectBatch = undefined;
    server = createServer((req, res) => {
      const url = new URL(req.url ?? '/', 'http://localhost');
      const ids = url.searchParams.getAll('ids');
      batches.push(ids);
      res.setHeader('content-type', 'application/json');
      if (req.method !== 'GET' || url.pathname !== '/api/conversations') {
        res.writeHead(404).end();
      } else if (ids.length > 99) {
        res.writeHead(422).end(JSON.stringify({ detail: 'Too many conversation IDs' }));
      } else if (batches.length === rejectBatch) {
        res.writeHead(503).end(JSON.stringify({ detail: 'Unavailable' }));
      } else {
        res.end(JSON.stringify(ids.map((id) => (id.endsWith('7') ? null : { id }))));
      }
    });
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    host = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });

  afterEach(async () => {
    server.closeAllConnections();
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve()))
    );
  });

  it.each([0, 99, 100, 114, 198, 215])(
    'retrieves %i IDs without exceeding the batch limit',
    async (count) => {
      const ids = Array.from(
        { length: count },
        (_, index) => `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`
      );
      if (count > 100) ids[100] = ids[0];

      const result = await new Client({ host }).getConversations(ids);

      expect(result).toEqual(ids.map((id) => (id.endsWith('7') ? null : { id })));
      expect(batches.flat()).toEqual(ids);
      expect(batches).toHaveLength(Math.ceil(count / 99));
      expect(batches.every((batch) => batch.length > 0 && batch.length <= 99)).toBe(true);
    }
  );

  it('rejects the whole lookup if a later batch fails', async () => {
    rejectBatch = 2;
    const ids = Array.from(
      { length: 114 },
      (_, index) => `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`
    );

    await expect(new Client({ host }).getConversations(ids)).rejects.toMatchObject({
      constructor: HttpError,
      status: 503,
    });
  });
});
