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
 * These tests pin the new behaviour: the WebSocket modules never throw at
 * module load, and the "no WebSocket implementation" condition is reported
 * via the existing `onError` callback only when `start()` is called.
 *
 * Implementation note: the default Jest runner is CommonJS, so plain
 * `require('ws')` *succeeds* in tests and hides the bug. Each test below
 * uses `jest.isolateModules` + `jest.doMock('ws', () => { throw ... })` so
 * the module-load path is exercised against the same conditions a real
 * Node.js ESM consumer hits.
 */

const WEBSOCKET_MODULES = [
  '../events/websocket-client',
  '../events/bash-websocket-client',
] as const;

const mockWsAsUnavailable = (): void => {
  jest.doMock('ws', () => {
    throw new Error('ws is not available in this environment');
  });
};

describe('package imports do not crash when `ws` is unavailable', () => {
  describe.each(WEBSOCKET_MODULES)('%s', (modulePath) => {
    it('does not throw at module load', () => {
      expect(() => {
        jest.isolateModules(() => {
          mockWsAsUnavailable();
          // eslint-disable-next-line @typescript-eslint/no-require-imports
          require(modulePath);
        });
      }).not.toThrow();
    });
  });

  it('importing the package barrel does not throw when `ws` is unavailable', () => {
    // This is the exact failure agent-canvas hit:
    //   import { RemoteWorkspace } from "@openhands/typescript-client";
    // would crash because the barrel transitively loads the websocket modules.
    expect(() => {
      jest.isolateModules(() => {
        mockWsAsUnavailable();
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

  it('constructing RemoteWorkspace does not require `ws`', () => {
    expect(() => {
      jest.isolateModules(() => {
        mockWsAsUnavailable();
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

  it('WebSocketCallbackClient.start() reports the missing implementation via onError instead of throwing', () => {
    let captured: Error | undefined;

    jest.isolateModules(() => {
      mockWsAsUnavailable();
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { WebSocketCallbackClient } = require('../events/websocket-client');
      const client = new WebSocketCallbackClient({
        host: 'http://example.com',
        conversationId: 'conv-1',
        callback: () => {},
        onError: (err: Error) => {
          captured = err;
        },
      });
      try {
        client.start();
      } finally {
        client.stop();
      }
    });

    expect(captured).toBeInstanceOf(Error);
    expect(captured?.message).toMatch(/WebSocket implementation not available/i);
  });

  it('BashWebSocketClient.start() reports the missing implementation via onError instead of throwing', () => {
    let captured: Error | undefined;

    jest.isolateModules(() => {
      mockWsAsUnavailable();
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { BashWebSocketClient } = require('../events/bash-websocket-client');
      const client = new BashWebSocketClient({
        host: 'http://example.com',
        callback: () => {},
        onError: (err: Error) => {
          captured = err;
        },
      });
      try {
        client.start();
      } finally {
        client.stop();
      }
    });

    expect(captured).toBeInstanceOf(Error);
    expect(captured?.message).toMatch(/WebSocket implementation not available/i);
  });
});
