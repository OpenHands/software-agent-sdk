import { once } from 'node:events';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { HttpClient, HttpError } from '../client/http-client';

let server: Server;
let client: HttpClient;

beforeEach(async () => {
  server = createServer();
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const { port } = server.address() as AddressInfo;
  client = new HttpClient({ baseUrl: `http://127.0.0.1:${port}`, timeout: 1000 });
});

afterEach(async () => {
  server.closeAllConnections();
  await new Promise<void>((resolve) => server.close(() => resolve()));
});

it.each(['{"events":', '{"events":[]}'])(
  'rejects a truncated GET response containing %s',
  async (body) => {
    server.on('request', (_request, response) => {
      response.writeHead(200, { 'Content-Type': 'application/json', 'Content-Length': '100' });
      response.write(body);
      setImmediate(() => response.destroy());
    });

    let watchdog: ReturnType<typeof setTimeout> | undefined;
    try {
      const result = await Promise.race([
        client.get('/batch', { data: ['event-1'] }).then(
          () => ({ state: 'resolved' }),
          (error: Error) => ({ state: 'rejected', error })
        ),
        new Promise<{ state: string }>((resolve) => {
          watchdog = setTimeout(() => resolve({ state: 'still pending' }), 1500);
        }),
      ]);
      expect(result.state).toBe('rejected');
      expect(result).toMatchObject({ error: { cause: { code: 'ECONNRESET' } } });
    } finally {
      clearTimeout(watchdog);
    }
  }
);

it('keeps complete GET response data and request bodies intact', async () => {
  let requestBody = '';
  server.on('request', (request, response) => {
    request.on('data', (chunk) => {
      requestBody += chunk;
    });
    request.on('end', () => {
      response.setHeader('Content-Type', 'application/json');
      response.write('{"events":');
      response.end('[]}');
    });
  });

  const response = await client.get('/batch', { data: ['event-1'] });

  expect(response.data).toEqual({ events: [] });
  expect(requestBody).toBe('["event-1"]');
});

it('preserves complete HTTP error responses', async () => {
  server.on('request', (_request, response) => {
    response.writeHead(500, { 'Content-Type': 'application/json' });
    response.end('{"detail":"batch unavailable"}');
  });

  await expect(client.get('/batch', { data: ['event-1'] })).rejects.toMatchObject({
    name: 'HttpError',
    status: 500,
    response: { detail: 'batch unavailable' },
  } satisfies Partial<HttpError>);
});
