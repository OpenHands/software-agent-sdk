import { createServer } from 'node:http';
import { describe, expect, it } from 'vitest';
import { HttpError } from '../client/http-client';
import { CloudClient } from '../client/cloud-client';

describe('Cloud JSON media type casing', () => {
  for (const transport of ['direct', 'proxy'] as const) {
    for (const contentType of [
      'Application/JSON; charset=utf-8',
      'application/json',
      'text/plain',
    ]) {
      for (const status of [200, 400]) {
        it(`${transport} reads ${contentType} at status ${status}`, async () => {
          const body = { detail: 'Keep My Case' };
          let received: { method?: string; path?: string } | undefined;
          const server = createServer((request, response) => {
            received = { method: request.method, path: request.url };
            request.resume();
            request.on('end', () => {
              response.writeHead(status, { 'Content-Type': contentType });
              response.end(JSON.stringify(body));
            });
          });
          await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
          const address = server.address();
          if (!address || typeof address === 'string') throw new Error('Missing loopback port');
          const host = `http://127.0.0.1:${address.port}`;
          const client = new CloudClient({ host, proxy: { host } });
          const expected = contentType === 'text/plain' ? JSON.stringify(body) : body;
          try {
            const request = client.request({
              method: 'GET',
              path: '/response',
              ...(transport === 'proxy' ? { hostOverride: 'https://sandbox.example.invalid' } : {}),
            });
            if (status === 200) {
              expect(await request).toEqual(expected);
            } else {
              const error = await request.catch((value: unknown) => value);
              expect(error).toBeInstanceOf(HttpError);
              expect((error as HttpError).response).toEqual(expected);
              expect((error as HttpError).detail).toBe(
                contentType === 'text/plain' ? undefined : body.detail
              );
            }
            expect(received).toEqual({
              method: transport === 'proxy' ? 'POST' : 'GET',
              path: transport === 'proxy' ? '/api/cloud-proxy' : '/response',
            });
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
