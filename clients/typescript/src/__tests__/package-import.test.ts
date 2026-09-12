/**
 * Regression tests for the package import surface.
 *
 * The package previously threw at module load whenever `ws` could not be
 * required (which is every Node.js ESM consumer, because `require` is not
 * defined in ESM). The throw lived inside `events/websocket-client.ts` and
 * `events/bash-websocket-client.ts`, both of which are transitively imported
 * by the package barrel via `RemoteConversation`. As a result, even a
 * consumer that only wanted `RemoteWorkspace` would crash on the very first
 * line:
 *
 *     import { RemoteWorkspace } from "@openhands/typescript-client";
 *
 * The `ws` fallback is gone. Every runtime this package supports supplies a
 * standards-compatible WebSocket global, so the clients read
 * `globalThis.WebSocket` directly.
 *
 * These tests pin two things: the WebSocket modules never throw at module
 * load even when no implementation exists, and the "no WebSocket
 * implementation" condition is reported via the existing `onError` callback
 * only when `start()` is called. Deleting the global is the real condition a
 * consumer would hit, rather than a simulation of it.
 */

const WEBSOCKET_MODULES = [
  '../events/websocket-client',
  '../events/bash-websocket-client',
] as const;

const originalWebSocket = globalThis.WebSocket;

/** Remove the global before the module under test reads it at load time. */
const removeWebSocketGlobal = (): void => {
  globalThis.WebSocket = undefined as unknown as typeof WebSocket;
};

afterEach(() => {
  globalThis.WebSocket = originalWebSocket;
  jest.resetModules();
});

describe('package imports do not crash without a WebSocket implementation', () => {
  describe.each(WEBSOCKET_MODULES)('%s', (modulePath) => {
    it('does not throw at module load', () => {
      expect(() => {
        jest.isolateModules(() => {
          removeWebSocketGlobal();
          // eslint-disable-next-line @typescript-eslint/no-require-imports
          require(modulePath);
        });
      }).not.toThrow();
    });
  });

  it('importing the package barrel does not throw', () => {
    // This is the exact failure agent-canvas hit:
    //   import { RemoteWorkspace } from "@openhands/typescript-client";
    // would crash because the barrel transitively loads the websocket modules.
    expect(() => {
      jest.isolateModules(() => {
        removeWebSocketGlobal();
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        const pkg = require('../index');
        // Touch a non-WebSocket export to make sure nothing is lazy in a way
        // that defers the throw past the import statement itself.
        expect(pkg.RemoteWorkspace).toBeDefined();
        expect(pkg.Agent).toBeDefined();
        expect(pkg.RemoteConversation).toBeDefined();
      });
    }).not.toThrow();
  });

  it('constructing RemoteWorkspace does not need a WebSocket', () => {
    expect(() => {
      jest.isolateModules(() => {
        removeWebSocketGlobal();
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        const { RemoteWorkspace } = require('../index');
        new RemoteWorkspace({
          host: 'https://agent.example.com',
          workingDir: '/workspace',
          apiKey: 'secret-key',
        });
      });
    }).not.toThrow();
  });

  it.each([
    ['../events/websocket-client', 'WebSocketCallbackClient', { conversationId: 'conv-1' }],
    ['../events/bash-websocket-client', 'BashWebSocketClient', {}],
  ])(
    '%s %s.start() reports the missing implementation via onError',
    (modulePath, exportName, extra) => {
      let captured: Error | undefined;

      jest.isolateModules(() => {
        removeWebSocketGlobal();
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        const module = require(modulePath);
        const client = new module[exportName]({
          host: 'http://example.com',
          callback: () => {},
          onError: (err: Error) => {
            captured = err;
          },
          ...(extra as Record<string, unknown>),
        });
        try {
          client.start();
        } finally {
          client.stop();
        }
      });

      expect(captured).toBeInstanceOf(Error);
      expect(captured?.message).toMatch(/WebSocket implementation not available/i);
    }
  );
});

describe('the clients use globalThis.WebSocket when it exists', () => {
  it.each([
    {
      modulePath: '../events/websocket-client',
      exportName: 'WebSocketCallbackClient',
      options: { host: 'http://example.com', conversationId: 'conv-1', callback: () => {} },
      expectedUrl: 'ws://example.com/sockets/events/conv-1',
    },
    {
      modulePath: '../events/bash-websocket-client',
      exportName: 'BashWebSocketClient',
      options: { host: 'http://example.com', callback: () => {} },
      expectedUrl: 'ws://example.com/sockets/bash-events',
    },
  ])('$exportName opens $expectedUrl', ({ modulePath, exportName, options, expectedUrl }) => {
    const urls: string[] = [];
    class FakeWebSocket {
      onopen?: () => void;
      onmessage?: (event: { data: unknown }) => void;
      onclose?: () => void;
      onerror?: () => void;

      constructor(url: string) {
        urls.push(url);
      }

      close(): void {}
    }
    globalThis.WebSocket = FakeWebSocket as unknown as typeof WebSocket;

    jest.isolateModules(() => {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const module = require(modulePath);
      const client = new module[exportName](options);
      client.start();
      client.stop();
    });

    expect(urls).toEqual([expectedUrl]);
  });
});
