# Remote stuck threshold propagation

Base: `ce5716a3c0a955a85bb0cc4dcfaa94d5fefcda50` (SDK 1.58.2).
Environment: WSL Linux, Python 3.13.17, committed uv lock.

The live regression runs a real Uvicorn server on loopback and the native
Agent/ThinkTool/run loop. Only LLM completion responses are scripted with TestLLM.
Seven identical think calls are followed by a finishing assistant message.

| Request | Base, newly created | Fixed, created / disk reloaded / server fork |
| --- | --- | --- |
| action_observation=2, enabled | 4 calls, stuck | 2 / 2 / 2 calls, stuck |
| action_observation=6, enabled | 4 calls, stuck | 6 / 6 / 6 calls, stuck |
| omitted, enabled | 4 calls, stuck | 4 / 4 / 4 calls, stuck |
| action_observation=2, disabled | 7 calls, finished | 7 / 7 / 7 calls, finished |
| action_observation=0 | HTTP 201 | HTTP 422, no conversation created |

The omitted case also reloads metadata without the new field (older format).
The fresh ConversationService test verifies restart reconstruction of the detector.
A separately launched `python -m openhands.agent_server` process retained 6/8
thresholds after a process restart and server fork. The built TypeScript client
sent all four camelCase options to that server and read back 6/2/5/8 under the
canonical snake_case keys. That process was stopped and its port closed.

Reproduce from this contribution branch, without a provider key:

```sh
make build
LITELLM_LOCAL_MODEL_COST_MAP=True uv run --frozen pytest \
  tests/cross/test_remote_conversation_live_server.py -k stuck_threshold -q -s
LITELLM_LOCAL_MODEL_COST_MAP=True uv run --frozen pytest \
  tests/agent_server/test_conversation_service.py -q
make test-server-schema
(cd clients/typescript && npm ci && npm run lint && npm run build && npm test && npm run check:agent-server-api)
```

Results: focused live regression 5 passed; conversation service 140 passed;
combined live-server, detector/config and OpenAPI suites 59 passed, 1 skipped;
TypeScript 396 passed, build/lint passed (5 existing lint warnings).
OpenAPI deterministic export/type quality/Swagger validation and the pinned
TypeScript contract regeneration pass. Candidate OpenAPI generation exposes
StuckDetectionThresholds, and its threshold payload is mutually assignable to
the handwritten TypeScript payload. The checked-in generated contract remains
at its repository-required release pin; it will update through the release workflow.

No paid provider, real-model evaluation, GPU, Docker, or binary-image validation.
Detector heuristics and scan capacity are unchanged. The server fork regression
passes on main plus this patch alone; no dependency on local-fork PR #6239.
