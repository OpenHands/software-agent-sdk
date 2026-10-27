# Event timestamp masking validation

AI-assisted implementation and local validation for OpenHands/software-agent-sdk#6258.

## Scope

Only `Event.timestamp` gains the existing `SkipSecretMasking` metadata. It remains
an ordinary string on the wire, with the same default factory and description.
The ID exclusions from #6155 and the masking walker are unchanged. User payloads
(including dictionary keys named `timestamp` or `id`) retain their existing
masking behavior. No persisted history is repaired.

## Before and after

Local Linux/WSL, Python 3.13.17, repository uv environment and frozen lockfile.
Base: `d0d9d81324210414462fb676b377e0563a08a0cb` (the merged #6155 fix).
The same final regression tests were copied into an isolated base worktree.

```text
Base: 5 failed in 8.34s
Head: 5 passed in 7.54s
```

The live-server test uses real uvicorn HTTP/WebSocket transport, a real
RemoteConversation, file-editor execution against a synthetic file, the real
LLMSecurityAnalyzer and SecretRegistry, and on-disk conversation persistence.
Only model completions/title generation are scripted with TestLLM. The registered
synthetic secret is the current year, which occurs in the generated event timestamp
and the file content. No real credential or external provider is needed.

On base, the security-analysis event returned by events/search has a timestamp
starting with `<secret-hidden>-10-27T...`. Reusing that returned timestamp gives:

```text
GET /api/conversations/{id}/events/search?timestamp__lt=<returned timestamp>
422: datetime_from_date_parsing; invalid character in year
```

On head, search returns 200; both `timestamp__lt` and `timestamp__gte` queries
return 200 and partition the events at that cursor without losing events.
All returned timestamps parse. File-editor observation content remains redacted.
After closing the remote client without deleting the conversation, the test reads
the persisted JSON and opens an independent EventLog over the same directory:
both preserve the exact timestamp returned by the API.

The SDK tests separately verify byte-identical full timestamps through masking,
JSON round-trip and EventLog reload for SecurityAnalysisEvent, MessageEvent and
ObservationEvent. Analyzer/policy strings, nested details and observation text
are redacted; an unrelated model's `timestamp` remains redacted. SecretStr and
explicit skip markers keep their existing behavior inside a containing model.

## Copyable checks

From a checkout of this branch, run `make build`, then:

```bash
uv run pytest -q tests/sdk/conversation/test_secret_registry_timestamps.py \
  tests/cross/test_remote_conversation_live_server.py::test_secret_masked_timestamp_cursor_with_live_server

uv run pytest -q tests/sdk/conversation/test_secrets_manager.py \
  tests/sdk/conversation/test_secret_registry_id_fields.py \
  tests/sdk/conversation/test_secret_registry_model_masking.py \
  tests/sdk/conversation/test_secret_registry_timestamps.py \
  tests/cross/test_secret_masking_id_field_guard.py \
  tests/sdk/agent/test_security_analysis_event.py \
  tests/sdk/agent/test_async_secret_masking.py

uv run pre-commit run --files openhands-sdk/openhands/sdk/event/base.py \
  tests/sdk/conversation/test_secret_registry_timestamps.py \
  tests/cross/test_remote_conversation_live_server.py
```

The second command passed 74 tests (10 existing TestLLM cost-calculation warnings).
All changed Python files passed every applicable pre-commit hook, including Pyright.
An additional before/after JSON Schema comparison found exact equality for Event,
SecurityAnalysisEvent, MessageEvent and ObservationEvent.

To reproduce the original failure using these same tests without modifying this
checkout:

```bash
base_dir=$(mktemp -d)
git worktree add --detach "$base_dir" d0d9d81324210414462fb676b377e0563a08a0cb
cp tests/sdk/conversation/test_secret_registry_timestamps.py \
  "$base_dir/tests/sdk/conversation/"
cp tests/cross/test_remote_conversation_live_server.py "$base_dir/tests/cross/"
(cd "$base_dir" && uv sync --frozen --dev && uv run pytest -q \
  tests/sdk/conversation/test_secret_registry_timestamps.py \
  tests/cross/test_remote_conversation_live_server.py::test_secret_masked_timestamp_cursor_with_live_server)
```

The five tests are expected to fail on that base. If a local proxy intercepts
loopback traffic, set `NO_PROXY=127.0.0.1,localhost,::1` and the lowercase equivalent
and unset `ALL_PROXY`/`all_proxy`; that isolation was used for the recorded runs.

## Limits

This is local source validation with scripted model responses, not a production
store or Python 3.14 deployment validation. No GPU, Docker/binary build, external
model request or full repository suite was run. The reporter's event counts were
not independently inspected. No claim is made about recovering already-corrupted
timestamps. Human review and upstream CI are separate evidence.
