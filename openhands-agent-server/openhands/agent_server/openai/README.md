# OpenAI-compatible gateway

This package contains the agent-server implementation for the OpenAI-compatible API surface under `/v1`.

- `router.py` defines the FastAPI routes and maps OpenAI-style bearer authentication to the existing session key mechanism.
- `models.py` contains the small server-side request models and aliases the reusable OpenAI response models.
- `service.py` translates Chat Completions and Responses requests into OpenHands
  conversations, waits for completion, and returns OpenAI-shaped responses.

`POST /v1/responses` implements the stateless client flow: every request starts a
fresh OpenHands conversation, and clients carry context forward by replaying
input and output items with `store: false`. `previous_response_id` is rejected
until the gateway can preserve exact response-turn lineage and branching
semantics.

Send `Idempotency-Key` with a Chat Completions or Responses request if you may
send the same request again. Keep the same key, body, conversation header, and
observability headers. The gateway stores a claim before it starts the turn.
After a successful turn, the same request returns the saved response. A changed
request with the same key returns `409`.
Keys have one scope for this server. A change from one accepted session key to
another does not start a second turn. A request rejected before dispatch,
including a run-capacity `429`, releases its key so the caller can retry it.

If the first request is still active or its outcome is unknown, the same
request returns `409`, `X-Should-Retry: false`, and
`X-OpenHands-ServerConversation-ID`. Inspect that conversation before you send
a new turn. This includes a run that continues after a gateway `504`. The
gateway cannot rebuild its exact OpenAI response after that timeout.
If the gateway cannot save a completed result, it returns `503` with the
conversation ID and `X-Should-Retry: false`.

Claims remain for seven days. After that time, the same key can start a new
turn. The store accepts at most 10,000 claims during that period. It does not
remove an unexpired claim to make room for a new key. The server stores a
protected digest of the key, not the key text.

Not yet supported on the Responses surface:

- `previous_response_id` is rejected with `400`; replay input items instead.
- `store: true` is rejected with `400` because the gateway does not retain a
  retrievable Responses object or implement `GET /v1/responses/{id}`. The
  backing OpenHands conversation still follows the agent-server's normal
  persistence policy, so `store: false` is not a data-retention control.
- `stream: true` is rejected with `400` (typed streaming events are follow-up
  scope).
- `tools`, `tool_choice`, and `parallel_tool_calls` are accepted but ignored;
  OpenHands tool execution stays internal to the agent. `temperature` and other
  generation-tuning fields are likewise ignored. These are dropped rather than
  rejected because they do not break the stateless default flow, but clients
  should not rely on them having an effect.

The gateway intentionally stays separate from the native agent-server routers so the OpenAI compatibility layer can evolve without mixing protocol translation code into the core REST API modules.
