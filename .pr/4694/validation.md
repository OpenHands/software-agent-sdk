# PR #4694 validation report

Verdict: ready for review as a stateless-first Responses API gateway.

Validated source commit: `d07c896cc8e740403cd6dae6ed99459d01fd857d`

Base: upstream `main` at `6a1e4d0f08dcdd02786526a51b7965e1877008bc`

Validated at: `2026-09-09T05:32:14Z`

Environment: macOS 26.5.2, Python 3.13.11, OpenAI Python SDK 2.33.0.

## Review-driven changes

- Kept the earlier `store: true` review fix: it returns `400` instead of
  promising a retrievable response that the gateway cannot provide.
- Removed the conversation-ID approximation for `previous_response_id`. It did
  not identify an exact response turn, accepted forged suffixes, could branch
  from the wrong head, and reused the first conversation's model while reporting
  the later request's model. The gateway now returns `400` and tells clients to
  replay input items.
- Moved top-level `instructions` and system/developer input into the agent's real
  system context. They are no longer encoded as user text.
- Added an EventService run-completion primitive so fresh gateway requests wait
  through stop-hook retries and pending event publication, without cancelling
  the agent run if the HTTP waiter times out.
- Replaced the OpenAI SDK's full generic `Response` type as the FastAPI response
  schema with the gateway's supported response shape. The generic type imported
  another `Action` schema, renamed OpenHands' canonical `Action` component, and
  broke the existing OpenAPI discriminator contract.
- Corrected the documentation: `store: false` is protocol-level statelessness,
  not a data-retention control. The backing native OpenHands conversation still
  follows normal agent-server persistence.

## Evidence matrix

| Scenario | Result | Evidence |
| --- | --- | --- |
| Tests-only red commit followed by green implementation | PASS | [`automated-tests.txt`](automated-tests.txt) |
| EventService lifecycle, auth, OpenAPI contracts | PASS, 149 tests | [`automated-tests.txt`](automated-tests.txt) |
| Real TCP server with official SDK and deterministic LLM fixtures | PASS, 2 tests | [`automated-tests.txt`](automated-tests.txt) |
| Clean-environment agent-server stress suite | PASS, 13 tests | [`automated-tests.txt`](automated-tests.txt) |
| Hosted Sonnet through official `OpenAI` client | PASS | [`live-official-sdk.json`](live-official-sdk.json) |
| Replay actual output items into a fresh conversation | PASS | [`live-official-sdk.json`](live-official-sdk.json) |
| Restart with the old conversation storage absent, then replay client items | PASS | [`restart-replay.json`](restart-replay.json) |
| Three simultaneous hosted requests through `AsyncOpenAI` | PASS | [`concurrency.json`](concurrency.json) |
| Invalid bearer token | PASS, `401` | [`live-official-sdk.json`](live-official-sdk.json) |
| `previous_response_id`, `store: true`, and `stream: true` | PASS, explicit `400` | [`live-official-sdk.json`](live-official-sdk.json) |
| Unknown OpenHands profile | PASS, `404` | [`live-official-sdk.json`](live-official-sdk.json) |

The live runs used an isolated subprocess server, isolated settings/workspace/
conversation directories, a copied single profile, bearer authentication, and
the official sync and async OpenAI clients. Evidence is sanitized: it contains
no credentials, environment dump, raw profile data, raw conversation state, or
telemetry.

## Scope recommendation

Merge the stateless path in this PR. Clients can preserve context by sending the
prior user input plus the returned output items in the next request. This also
works after a server restart with no prior native conversation available.

Implement `previous_response_id` separately only with durable
response-ID-to-exact-turn mapping, restart recovery, old-turn branching/forking,
model and instruction replacement, exact-ID validation, and same-parent
concurrency tests. A conversation UUID alone is not sufficient.

Streaming is useful follow-up scope because agent runs are long-lived. Stored
Responses objects and a `GET /v1/responses/{id}` endpoint are lower priority than
streaming and exact continuation because native OpenHands conversations already
provide persistence. Unsupported OpenAI generation/tool-selection fields remain
documented as ignored; they should be implemented or explicitly rejected before
claiming broader Responses API compatibility.

## Contract references

- [Create a response](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)
- [Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)
