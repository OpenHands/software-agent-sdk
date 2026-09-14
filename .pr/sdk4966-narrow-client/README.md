# Live Canvas evidence for the narrowed runtime client

An isolated Agent Canvas used the #4966 TypeScript client to run a real
DeepSeek V4 Flash conversation against a local agent server. The agent ran
`pwd`, wrote `runtime-proof.txt`, read it back, and reported the exact contents.
Captured network responses show all three shell operations used
`/api/conversations/{conversation_id}/bash/execute_bash_command` with status
200. The workspace path also contained that conversation ID.

[Animated recording](live-canvas.gif)

The client was #4966 at `4244f91f0`, whose runtime implementation is unchanged
by the later OpenAPI-only `3600b6d38` and scoped-cleanup `cd064c08f` commits.
Canvas additionally needed its existing browser event-stream client commits
`dd056163b` and `2e59a53d1`; these are companion client functionality, not part
of #4966. Canvas was `e895bb22`. Automation was present only to serve the
isolated UI and had no schedules or runs. The stack permitted one worker and
was guarded against less than 3 GiB available memory.

This demonstrates the local runtime route through the actual UI and model. The
existing Docker recording linked from the PR description covers Docker routing;
the 59-test current descendant check covers scoped routers, Docker routers and
proxy behavior after this simplification.
