import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { ConversationClient } from '../client/conversation-client';

describe('ACP permission transport', () => {
  let server: Server;
  let host: string;
  const observed: { method: string; url: string; key: string | undefined; body: string }[] = [];
  beforeAll(async () => {
    server = createServer(async (request, response) => {
      let body = '';
      for await (const chunk of request) body += chunk;
      observed.push({
        method: request.method ?? '',
        url: request.url ?? '',
        key: request.headers['x-session-api-key'] as string | undefined,
        body,
      });
      response.setHeader('content-type', 'application/json');
      response.statusCode = request.url?.includes('stale') ? 404 : 200;
      response.end(JSON.stringify(request.method === 'GET' ? { items: [] } : { success: true }));
    });
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    host = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });
  afterAll(async () => {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });
  it('authenticates scoped decisions, preserves null cancellation and rejects stale responses', async () => {
    const client = new ConversationClient({ host, apiKey: 'fixture-only' });
    await expect(client.listACPPermissions('conversation')).resolves.toEqual({ items: [] });
    await client.respondToACPPermission('conversation', 'request', { option_id: null });
    await expect(
      client.respondToACPPermission('conversation', 'stale', { option_id: 'offered' })
    ).rejects.toThrow();
    expect(observed.map((entry) => [entry.method, entry.url, entry.key])).toEqual([
      ['GET', '/api/conversations/conversation/acp/permissions', 'fixture-only'],
      ['POST', '/api/conversations/conversation/acp/permissions/request', 'fixture-only'],
      ['POST', '/api/conversations/conversation/acp/permissions/stale', 'fixture-only'],
    ]);
    expect(JSON.parse(observed[1].body)).toEqual({ option_id: null });
    client.close();
  });
});
