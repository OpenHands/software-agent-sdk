/**
 * Regression tests for the package import surface when `ws` is unavailable.
 */

import Module from 'node:module';

const WEBSOCKET_MODULES = [
  '../events/websocket-client',
  '../events/bash-websocket-client',
] as const;

const importWithoutWs = async <T>(modulePath: string): Promise<T> => {
  const require = Module.prototype.require;
  vi.spyOn(Module.prototype, 'require').mockImplementation(function (id: string) {
    if (id === 'ws') {
      throw new Error('ws is not available in this environment');
    }
    return require.call(this, id);
  });
  vi.resetModules();
  return import(modulePath) as Promise<T>;
};

describe('package imports do not crash when `ws` is unavailable', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.resetModules();
  });

  describe.each(WEBSOCKET_MODULES)('%s', (modulePath) => {
    it('does not throw at module load', async () => {
      await expect(importWithoutWs(modulePath)).resolves.toBeDefined();
    });
  });

  it('importing the package barrel does not throw when `ws` is unavailable', async () => {
    const pkg = await importWithoutWs<typeof import('../index')>('../index');

    expect(pkg.RemoteWorkspace).toBeDefined();
    expect(pkg.Agent).toBeDefined();
    expect(pkg.RemoteConversation).toBeDefined();
  });

  it('constructing RemoteWorkspace does not require `ws`', async () => {
    const { RemoteWorkspace } = await importWithoutWs<typeof import('../index')>('../index');

    expect(
      () =>
        new RemoteWorkspace({
          host: 'https://agent.example.com',
          workingDir: '/workspace',
          apiKey: 'secret-key',
        })
    ).not.toThrow();
  });

  it('WebSocketCallbackClient.start() reports the missing implementation via onError', async () => {
    const { WebSocketCallbackClient } = await importWithoutWs<
      typeof import('../events/websocket-client')
    >('../events/websocket-client');
    let captured: Error | undefined;
    const client = new WebSocketCallbackClient({
      host: 'http://example.com',
      conversationId: 'conv-1',
      callback: () => {},
      onError: (error) => {
        captured = error;
      },
    });

    try {
      client.start();
    } finally {
      client.stop();
    }

    expect(captured).toBeInstanceOf(Error);
    expect(captured?.message).toMatch(/WebSocket implementation not available/i);
  });

  it('BashWebSocketClient.start() reports the missing implementation via onError', async () => {
    const { BashWebSocketClient } = await importWithoutWs<
      typeof import('../events/bash-websocket-client')
    >('../events/bash-websocket-client');
    let captured: Error | undefined;
    const client = new BashWebSocketClient({
      host: 'http://example.com',
      callback: () => {},
      onError: (error) => {
        captured = error;
      },
    });

    try {
      client.start();
    } finally {
      client.stop();
    }

    expect(captured).toBeInstanceOf(Error);
    expect(captured?.message).toMatch(/WebSocket implementation not available/i);
  });
});
