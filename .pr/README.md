# Live run-limit evidence (#4063 / #4389)

Six ordinary Agent Server conversations were submitted concurrently over HTTP,
with `max_concurrent_runs=2`. Each agent made one delayed completion request to a
local OpenAI-compatible HTTP provider. The provider counted overlapping requests.
No server, conversation, agent, or admission method was mocked or patched.

| Server source | Configured limit | Conversations | Peak active requests | Finished |
| --- | ---: | ---: | ---: | ---: |
| Base `3311ba9eec5044f40ab5d0b3d7eddc9f7e1e2d14` | 2 | 6 | **6** | 6 |
| Fixed `d1f9772e9a6df1e15487d9a5833e2f80903d910a` | 2 | 6 | **2** | 6 |
| Fixed, alternate limit | 1 | 4 | **1** | 4 |

The JSON files contain the revision, effective config, server PID/URL,
conversation IDs, final states, and the provider's timestamped enter/exit trace:
[before.json](before.json), [after.json](after.json),
[after-limit-one.json](after-limit-one.json).
The recorded diff hash for a clean checkout is SHA256 of the empty byte string.

## Reproduce

From the PR checkout, after `uv sync --frozen --group dev`:

```bash
git worktree add --detach ../sdk-before-run-limit 3311ba9eec5044f40ab5d0b3d7eddc9f7e1e2d14
uv run python .pr/run_limit_live.py --checkout ../sdk-before-run-limit \
  --expect exceeded --output .pr/before.json
uv run python .pr/run_limit_live.py --checkout . \
  --expect respected --output .pr/after.json
uv run python .pr/run_limit_live.py --checkout . --expect respected \
  --limit 1 --conversations 4 --output .pr/after-limit-one.json
```

Observed output:

```text
PASS: expected=exceeded, limit=2, peak=6, completed=6; .pr/before.json
PASS: expected=respected, limit=2, peak=2, completed=6; .pr/after.json
```

The script exits nonzero for an unexpected peak, failed conversation, missing
provider request, startup failure, or timeout. Running the base with
`--expect respected` fails its concurrency assertion. It creates isolated temp
workspaces and launches/stops its own processes; it needs no API keys. Full
server logs are written beside each JSON file with `.server.log` suffix.

## Regression checks

```bash
uv run pytest tests/agent_server/test_run_admission.py \
  tests/agent_server/test_event_service.py \
  tests/agent_server/test_conversation_service.py -q --timeout=60
```

Result: **242 passed**. The eleven admission cases cover async, sync, and mixed
runs; capacity reuse after completion/errors; queued pause/interrupt/close;
cancellation of an active async run; retaining a cancelled synchronous worker's
slot until its thread exits; and a pause before the background task starts.
All pre-commit checks passed for the changed files.

## Scope and environment

Run on Linux x86_64 with Python 3.13.13 and the locked SDK workspace dependencies.
The server is a real subprocess reached through REST, running the standard
`Agent` / `LocalConversation` async path. Only the external model provider is a
deterministic delayed fixture; this does not evaluate model quality or tool load.

This is a **per-Agent-Server execution limit**. The OSS VM uses one inner Agent
Server per Docker conversation, so this evidence does **not** demonstrate a
host-wide Docker container limit. Docker provisioning and backlog admission are
separate controls; this PR does not add a queue API or runtime admission policy.
