# Live verification

On Node 22.23.3, a direct connection to a closed port fires only `error`, not
`close`:

```text
v22.23.3
error
```

With the fixed client connected to a real agent-server, stopping the server
produced repeated refused handshakes. Starting the server again allowed the
same stream to reconnect:

```text
{"isConnected":false,"isReconnecting":true,"attemptCount":1,...code 1012...}
{"isConnected":false,"isReconnecting":true,"attemptCount":2,"error":"WebSocket connection failed before opening"}
{"isConnected":false,"isReconnecting":true,"attemptCount":3,"error":"WebSocket connection failed before opening"}
{"isConnected":false,"isReconnecting":true,"attemptCount":4,"error":"WebSocket connection failed before opening"}
{"isConnected":false,"isReconnecting":true,"attemptCount":5,"error":"WebSocket connection failed before opening"}
{"isConnected":false,"isReconnecting":true,"attemptCount":6,"error":"WebSocket connection failed before opening"}
{"isConnected":true,"isReconnecting":false,"attemptCount":0,"error":null}
```

A fake transport that emits `error` followed by `close` now reports one
`onClose` callback and schedules only one retry.
