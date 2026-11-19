import { once } from 'node:events';
import { createServer } from 'node:http';
import { CloudClient } from '../clients';

describe('device authorization polling deadline', () => {
  it.each(['headers', 'body', 'interval'] as const)(
    'times out while waiting for %s',
    async (waitingFor) => {
      let requests = 0;
      const server = createServer((_request, response) => {
        requests += 1;
        if (waitingFor === 'headers') return;
        response.writeHead(400, { 'Content-Type': 'application/json' });
        if (waitingFor === 'body') {
          response.write('{"error":');
        } else {
          response.end('{"error":"authorization_pending"}');
        }
      });
      server.listen(0, '127.0.0.1');
      await once(server, 'listening');
      const address = server.address();
      if (!address || typeof address === 'string') throw new Error('Missing server address');
      const client = new CloudClient({ host: `http://127.0.0.1:${address.port}` });
      const controller = new AbortController();
      let watchdog: ReturnType<typeof setTimeout> | undefined;

      try {
        const polling = client.pollForToken('device-code', {
          interval: 30,
          timeout: 100,
          signal: waitingFor === 'headers' ? undefined : controller.signal,
        });
        await expect(
          Promise.race([
            polling,
            new Promise((_, reject) => {
              watchdog = setTimeout(() => reject(new Error('Polling missed its deadline')), 1000);
            }),
          ])
        ).rejects.toMatchObject({ name: 'DeviceFlowError', code: 'timeout' });
        expect(requests).toBe(1);
        expect(controller.signal.aborted).toBe(false);
      } finally {
        clearTimeout(watchdog);
        controller.abort();
        server.closeAllConnections();
        await new Promise<void>((resolve) => server.close(() => resolve()));
      }
    }
  );

  it.each(['timeout', 'cancelled'] as const)(
    'preserves the first abort reason: %s',
    async (code) => {
      const deadline = new AbortController();
      const caller = new AbortController();
      const timeoutReason = new DOMException('Deadline reached', 'TimeoutError');
      const callerReason = new Error('Cancelled by caller');
      vi.spyOn(AbortSignal, 'timeout').mockReturnValue(deadline.signal);
      vi.stubGlobal(
        'fetch',
        vi.fn(
          (_input, init: RequestInit) =>
            new Promise((_, reject) => {
              const signal = init.signal;
              if (!signal) throw new Error('Missing polling signal');
              signal.addEventListener('abort', () => reject(signal.reason), { once: true });
              if (code === 'timeout') {
                deadline.abort(timeoutReason);
                caller.abort(callerReason);
              } else {
                caller.abort(callerReason);
                deadline.abort(timeoutReason);
              }
            })
        )
      );

      try {
        const client = new CloudClient({ host: 'https://cloud.example.com' });
        await expect(
          client.pollForToken('device-code', { interval: 1, signal: caller.signal })
        ).rejects.toMatchObject({ name: 'DeviceFlowError', code });
      } finally {
        vi.restoreAllMocks();
        vi.unstubAllGlobals();
      }
    }
  );

  it('does not send a token request when the timeout is zero', async () => {
    vi.stubGlobal('fetch', vi.fn());
    try {
      const client = new CloudClient({ host: 'https://cloud.example.com' });
      await expect(
        client.pollForToken('device-code', { interval: 1, timeout: 0 })
      ).rejects.toMatchObject({ name: 'DeviceFlowError', code: 'timeout' });
      expect(fetch).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
