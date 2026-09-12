# Browser event stream transport

Canvas currently owns the raw WebSocket, first-frame auth, retry timers, and handshake cleanup. This API moves that transport into the SDK secondary clients entrypoint. Canvas keeps event interpretation and React state. The existing higher-level WebSocketCallbackClient remains unchanged to preserve its established callback/API behavior.

Validation:
- TypeScript build and ESLint passed.
- 10 transport regression tests and six package-import regressions passed.
- Live isolated Agent Server: conversation `81b5400b-259b-4076-9986-a23c6e3afdea`, two authenticated opens (including explicit reconnect), two parsed event frames, no server error frames. See event-stream-live.json. Credentials were supplied in the first frame and are not in this artifact.
- Downstream Canvas adapter: 44 tests passed, four existing skips, including main/planner routing and REST fallback.

Reproduce with a local Agent Server and the README example, using its session credential and an existing conversation. Connect, receive replay, call reconnect, then stop. The same transport is used for local and Docker conversations.

The repository-wide dynamic-attribute pre-commit check fails on four unchanged Python calls in stream_context.py and telemetry.py on the base commit. That unrelated hook was skipped; all applicable hooks, TypeScript lint/build/tests passed.
