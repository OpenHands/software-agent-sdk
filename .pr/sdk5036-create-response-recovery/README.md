# Live Canvas create-response recovery

This records the same synthetic transport fault before and after SDK #5036. An
isolated Agent Canvas used a real local agent server and DeepSeek V4 Flash. The
browser proxy first forwarded `POST /api/conversations` and received `201`, then
deliberately dropped that response. This models a response lost after the server
has already created and started the conversation.

- **Before:** the client from SDK #4966 without #5036 posted exactly once. Canvas
  showed `Disconnected` errors and did not open the created conversation. The
  server-created conversation still ran and later appeared in the sidebar.
- **After:** the same client plus #5036 posted exactly once, reconciled the
  caller-supplied conversation ID with GET, showed no error, and the real agent
  completed the task. The create POST was never replayed.

[Before recording](before.gif) · [After recording](after.gif)

The Canvas build also includes the existing browser event-stream commits
`dd056163b` and `2e59a53d1`, which Canvas already consumes. The agent-server was
SDK #4966 at `4244f91f0`; the later `3600b6d38` only changes OpenAPI metadata.
The after client adds #5036 commit `e27c6d248`. Automation was present only to
serve the isolated Canvas setup and had no schedules or runs. The resource guard
required 3 GiB available memory; the worker limit was one.

This is deliberate fault injection at the browser transport boundary. It proves
the lost-response path and the absence of a duplicate POST; it does not simulate
every browser or network failure.
