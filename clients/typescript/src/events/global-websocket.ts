/**
 * WebSocket constructor lookup shared by the callback-style socket clients.
 */

/**
 * The Web-standard WebSocket constructor of the current runtime: browsers,
 * web workers and Node 22+ all expose it as `globalThis.WebSocket`.
 *
 * Looked up on every connect, never at import time, so importing the package
 * barrel neither throws nor pulls in a Node-only module, and a constructor
 * installed on `globalThis` after import (for example the `ws` package on an
 * older Node) is picked up.
 */
export function getGlobalWebSocket(): (new (url: string) => WebSocket) | undefined {
  return (globalThis as { WebSocket?: new (url: string) => WebSocket }).WebSocket;
}

export const WEBSOCKET_UNAVAILABLE_MESSAGE =
  'WebSocket implementation not available. Run in an environment with a global ' +
  'WebSocket constructor (browsers, Node 22+), or assign one such as the `ws` ' +
  'package to globalThis.WebSocket.';
