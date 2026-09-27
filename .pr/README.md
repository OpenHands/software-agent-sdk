# Live evidence: conversation admission (#4063 / #5350)

The same script exercises an unmodified Agent Server subprocess over HTTP on
base `3311ba9eec5044f40ab5d0b3d7eddc9f7e1e2d14` and fixed
`bcbea576d8d3caca067e8c5cb274f6e314e53cb4`. Ordinary agents call a delayed local
OpenAI-compatible provider, which counts simultaneous completion requests.
No server, agent, conversation, or admission code is mocked or patched.

| Revision / endpoint | Limit | Requests | Initially admitted | HTTP 429 | Peak executing | Eventually finished |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Base / run | 2 | 6 | 6 | 0 | **6** | 6 |
| Base / create-and-run | 2 | 6 | 6 | 0 | **6** | 6 |
| Fixed / run | 2 | 6 | 2 | 4 | **2** | 6 |
| Fixed / create-and-run | 2 | 6 | 2 | 4 | **2** | 6 |
| Fixed / create-and-run | 1 | 4 | 1 | 3 | **1** | 4 |

The client retries rejected requests; the server does not queue them. Before
retrying a rejected creation, the script verifies that its conversation returns
404 and its UUID-hex persistence directory does not exist.

Results: [before](before.json), [before create](before-create.json),
[after](after.json), [after create](after-create.json),
[after limit=1](after-limit-one.json). Each includes the revision, config,
HTTP responses, conversation IDs, final states, and timestamped provider trace.
The empty diff SHA256 confirms each server checkout was clean during the run.

## Reproduce

From this checkout, after `uv sync --frozen --group dev`:

```bash
git worktree add --detach ../sdk-before-run-limit 3311ba9eec5044f40ab5d0b3d7eddc9f7e1e2d14
uv run python .pr/run_limit_live.py --checkout ../sdk-before-run-limit \
  --expect exceeded --output /tmp/before.json
uv run python .pr/run_limit_live.py --checkout . \
  --expect respected --output /tmp/after.json
```

Repeat both commands with `--entrypoint create` to test create-and-run. Add
`--limit 1 --conversations 4` to exercise a different capacity. Running the base
with `--expect respected` fails. The script needs no credentials and cleans up
its isolated processes/workspace; full logs are written next to its JSON output.

## Regression checks and limits

**378 passed** across `test_run_admission.py`, `test_event_service.py`,
`test_conversation_service.py`, `test_goal_loop.py`, `test_conversation_router.py`,
and `test_event_router.py` under `tests/agent_server/`. The 13 focused admission
cases cover async/sync/mixed execution, burst rejection, initialization failure,
reattachment while full, capacity reuse, message retention, goal rejection,
and cancellation before startup or while a sync worker is still running.
Changed-file pre-commit checks passed.

The parallelism stress benchmark now explicitly configures capacity for its
16-conversation workload. Locally its wall-clock assertion failed on both the
unchanged base (0.89s vs 0.75s budget) and this branch (1.61s vs 0.81s); the timing
threshold was not relaxed. CI runs the full stress suite separately.

Environment: Linux x86_64, Python 3.13.13, locked workspace dependencies. This
proves **per-Agent-Server admission**, using a deterministic model substitute.
It does not establish a host-wide Docker-container limit or measure real model
quality/tool memory consumption. No production VM was changed.
