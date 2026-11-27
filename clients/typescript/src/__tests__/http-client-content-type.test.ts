import { createServer } from 'node:http';
import { describe, expect, it } from 'vitest';
import { HttpClient, HttpError } from '../client/http-client';

describe('HTTP JSON media type casing', () => {
  for (const transport of ['fetch', 'get-body'] as const) {
    for (const contentType of [
      'Application/JSON; charset=utf-8',
      'application/json',
      'text/plain',
    ]) {
      for (const status of [200, 400]) {
        it(`${transport} reads ${contentType} at status ${status}`, async () => {
          const body = { detail: 'Keep My Case' };
          const server = createServer((request, response) => {
            request.resume();
            request.on('end', () => {
              response.writeHead(status, { 'Content-Type': contentType });
              response.end(JSON.stringify(body));
            });
          });
          await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
          const address = server.address();
          if (!address || typeof address === 'string') throw new Error('Missing loopback port');
          const client = new HttpClient({ baseUrl: `http://127.0.0.1:${address.port}` });
          const expected = contentType === 'text/plain' ? JSON.stringify(body) : body;
          try {
            const request = client.get(
              '/response',
              transport === 'get-body' ? { data: { ids: [1] } } : {}
            );
            if (status === 200) {
              expect((await request).data).toEqual(expected);
            } else {
              const error = await request.catch((value: unknown) => value);
              expect(error).toBeInstanceOf(HttpError);
              expect((error as HttpError).response).toEqual(expected);
              expect((error as HttpError).detail).toBe(
                contentType === 'text/plain' ? undefined : body.detail
              );
            }
          } finally {
            server.closeAllConnections();
            await new Promise<void>((resolve, reject) =>
              server.close((error) => (error ? reject(error) : resolve()))
            );
          }
        });
      }
    }
  }
});
