import { createServer } from 'node:http';
import { CloudClient } from '../client/cloud-client';

describe('CloudClient proxy transport', () => {
  it('forwards a confirmation JSON body as text through a strict HTTP proxy', async () => {
    const server = createServer(async (request, response) => {
      try {
        request.setEncoding('utf8');
        let body = '';
        for await (const chunk of request) body += chunk;

        if (request.url === '/api/cloud-proxy') {
          // Mirror the proxy's string-or-null body contract and verbatim forwarding.
          const envelope = JSON.parse(body);
          if (envelope.body !== null && typeof envelope.body !== 'string') {
            response.writeHead(422, { 'Content-Type': 'application/json' });
            response.end(JSON.stringify({ detail: 'body must be a string or null' }));
            return;
          }
          const upstream = await fetch(`${envelope.host}${envelope.path}`, {
            method: envelope.method,
            headers: envelope.headers,
            body: envelope.body,
          });
          response.writeHead(upstream.status, { 'Content-Type': 'application/json' });
          response.end(await upstream.text());
          return;
        }

        if (request.url === '/api/conversations/c1/events/respond_to_confirmation') {
          response.writeHead(200, { 'Content-Type': 'application/json' });
          response.end(
            JSON.stringify({
              body,
              contentType: request.headers['content-type'],
              sessionApiKey: request.headers['x-session-api-key'],
            })
          );
          return;
        }
        response.writeHead(404).end();
      } catch {
        response.writeHead(500).end();
      }
    });

    await new Promise<void>((resolve, reject) => {
      server.once('error', reject);
      server.listen(0, '127.0.0.1', resolve);
    });
    try {
      const address = server.address();
      if (!address || typeof address === 'string') throw new Error('Expected a TCP address');
      const host = `http://127.0.0.1:${address.port}`;
      const client = new CloudClient({
        host,
        proxy: { host, apiKey: 'proxy-key' },
      });

      const result = await client.request({
        method: 'POST',
        hostOverride: host,
        path: '/api/conversations/c1/events/respond_to_confirmation',
        body: { accept: true },
        authMode: 'session-api-key',
        sessionApiKey: 'runtime-key',
      });

      expect(result).toEqual({
        body: '{"accept":true}',
        contentType: 'application/json',
        sessionApiKey: 'runtime-key',
      });
    } finally {
      await new Promise<void>((resolve) => {
        server.close(() => resolve());
        server.closeAllConnections();
      });
    }
  });
});
