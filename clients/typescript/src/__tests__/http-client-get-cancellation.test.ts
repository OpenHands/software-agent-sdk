import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';

import { HttpClient } from '../client/http-client';

let server: Server;
let client: HttpClient;

beforeEach(async () => {
  server = createServer();
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;
  client = new HttpClient({ baseUrl: `http://127.0.0.1:${port}`, timeout: 250 });
});

afterEach(async () => {
  server.closeAllConnections();
  await new Promise<void>((resolve, reject) =>
    server.close((error) => (error ? reject(error) : resolve()))
  );
});

it('cancels a GET with a JSON body when its owner has already aborted', async () => {
  const controller = new AbortController();
  controller.abort();

  await expect(
    client.get('/events', { data: { ids: ['event-1'] }, signal: controller.signal })
  ).rejects.toMatchObject({ name: 'AbortError' });
});

it('cancels an in-flight GET with a JSON body and closes its connection', async () => {
  let requestReceived!: () => void;
  let connectionClosed!: () => void;
  const received = new Promise<void>((resolve) => (requestReceived = resolve));
  const closed = new Promise<void>((resolve) => (connectionClosed = resolve));
  server.once('request', (_request, response) => {
    response.once('close', connectionClosed);
    requestReceived();
  });

  const controller = new AbortController();
  const pending = client.get('/events', {
    data: { ids: ['event-1'] },
    signal: controller.signal,
  });
  const rejection = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  await received;
  controller.abort();

  await rejection;
  await closed;
});

it('still returns a successful GET-with-body response without cancellation', async () => {
  server.once('request', (_request, response) => {
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify({ events: [] }));
  });

  const response = await client.get('/events', { data: { ids: ['event-1'] } });

  expect(response.status).toBe(200);
  expect(response.data).toEqual({ events: [] });
});
