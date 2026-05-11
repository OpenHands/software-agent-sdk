# openhands-agent-server

See the [project root AGENTS.md](../AGENTS.md) for repository-wide policies and workflows.

## Development

This package lives in the monorepo root. Typical commands (run from repo root):

- Install deps: `make build`
- Run agent-server tests: `uv run pytest tests/agent_server`

## PyInstaller data files

When adding non-Python files (JS, templates, etc.) loaded at runtime, add them to `openhands-agent-server/openhands/agent_server/agent-server.spec` using `collect_data_files`.


## Stress / scale tests

`tests/agent_server/stress/` is an in-process stress suite that exercises
agent-server failure modes at realistic scale — parallel sub-agents, many
conversations, long-running bash, slow webhooks, websocket back-pressure, etc.

### Running stress tests

The suite is **excluded from default collection** via `addopts = -m 'not stress'`
in `pyproject.toml`. Override the filter with `-m stress`:

```bash
# Run the full stress suite (~3–5 min on a developer laptop)
uv run pytest -m stress

# Run a single stress test file
uv run pytest -m stress tests/agent_server/stress/test_conversation_listing.py

# Verify stress tests are deselected by default
uv run pytest --collect-only -q  # stress tests appear as "deselected"
```

**Note:** a bare `pytest tests/agent_server/stress/` will collect-then-deselect
because the `addopts` filter still applies — always pass `-m stress` alongside
the path for a path-scoped run.

### How the test infrastructure works

Tests run **in-process** against the agent-server FastAPI app — no real binary,
no real network, no real LLM. The key fixtures (in `conftest.py`) are:

| Fixture | Purpose |
|---|---|
| `conversation_service` | Real `ConversationService` pointed at `tmp_path/persist` |
| `bash_service` | Per-test `BashEventService`, monkeypatched into the bash router |
| `app` | FastAPI app wired to the test services via dependency override |
| `client` | `httpx.AsyncClient` over `ASGITransport` (shares the test event loop) |
| `probe` | `ResourceProbe` — psutil-backed background sampler for RSS, FDs, threads, CPU |

**Why TestLLM needs a workaround:** `StartConversationRequest` round-trips
through JSON (`model_dump` → revalidate), which strips `TestLLM`'s private
`_scripted_responses`. Tests use `placeholder_llm()` for the request, then call
`conversation.switch_llm(real_test_llm)` after creation. This pattern is in
`scripts.start_conversation_with_test_llm()`.

### Layout

| File | Role |
|---|---|
| `__init__.py` | Suite docstring and top-level documentation |
| `conftest.py` | Shared fixtures (service, app, client, probe) |
| `budgets.py` | Frozen dataclasses with assertion thresholds (latency, RSS, FDs, event counts) |
| `probe.py` | `ResourceProbe` — psutil background sampler for budget assertions |
| `scripts.py` | `SlowTestLLM`, `placeholder_llm()`, `start_conversation_with_test_llm()`, `wait_for_terminal()` |
| `test_*.py` | One file per failure mode |

### Adding a new stress test

1. **Create `test_<failure_mode>.py`** — one file per bug class. Start with a
   module docstring naming the bug class caught and any caveats.
2. **Add `pytestmark = pytest.mark.stress`** at module level so the test is
   deselected by default.
3. **Define a budget** in `budgets.py` as a frozen `@dataclass(frozen=True, slots=True)`.
   Prefer relative-to-baseline ratios (e.g., `rss_growth_factor`) over absolute
   numbers; absolute thresholds only for failure modes whose definition _is_
   unbounded growth. Add a module-level constant instance (e.g.,
   `MY_BUDGET = MyBudget()`).
4. **Use `conftest.py` fixtures** (`conversation_service`, `bash_service`, `client`,
   `probe`) — don't create ad-hoc services. If a test needs a custom app
   configuration (e.g., webhook config), override fixtures locally in the test file
   (see `test_slow_webhook.py` for an example).
5. **Use `scripts.py` helpers** for common operations:
   - `SlowTestLLM` — `TestLLM` with synthetic per-call latency (makes parallelism
     observable).
   - `start_conversation_with_test_llm()` — creates a conversation, installs the
     TestLLM, optionally queues an initial message.
   - `wait_for_terminal()` — polls conversation status until it reaches a terminal
     state.
6. **Assert against budgets**, not magic numbers. Include a diagnostic message in
   the `assert` explaining the likely regression (see existing tests for examples).
7. **POSIX-only** — the suite uses `psutil.num_fds()`, file locks, bash pipelines,
   and shell builtins. No Windows shims.

### Known-bug xfail markers

Known agent-server bugs are surfaced as `@pytest.mark.xfail(strict=True)` in
`tests/agent_server/test_*.py` (outside the stress directory). Each marker
includes a `reason` string with a description and a tracking issue link
(under [#3117](https://github.com/OpenHands/software-agent-sdk/issues/3117)).
If a test starts passing (`XPASS`), the bug is fixed and the marker should be
removed.

## Live server integration tests

Small endpoint additions or changes to server behaviour should be covered by a
test in `tests/cross/test_remote_conversation_live_server.py`.  These tests spin
up a real FastAPI server with a patched LLM and exercise the full HTTP / WebSocket
stack end-to-end.  Add or extend a test there whenever the change is localised
enough that a single new test function (or a few assertions added to an existing
test) captures the expected behaviour.


## Concurrency / async safety

- `ConversationState` uses a synchronous `FIFOLock`. In async agent-server code, never do `with conversation._state` directly on the event loop when the conversation may be running.
- WebSocket reconnects call `EventService.subscribe_to_events()` immediately; if initial state snapshot creation blocks on the state lock in async context, the whole FastAPI event loop can stop serving `/ready` and similar probes.
- The same rule applies to metadata updates in `ConversationService.update_conversation()`: keep the locked mutation/snapshot semantics, but move the synchronous lock wait into a worker thread first.
- In async routes/services, move state-lock acquisition into `run_in_executor(...)` (or another worker-thread boundary) before awaiting network I/O.


## REST API compatibility & deprecation policy

The agent-server **REST API** (the FastAPI OpenAPI surface under `/api/**`) is a
public API and must remain backward compatible across releases.

All REST contract breaks need a deprecation notice and a runway of
**5 minor releases** before removing the old contract or making an
incompatible replacement mandatory.

### Deprecating an endpoint

When deprecating a REST endpoint:

1. Mark the operation as deprecated in OpenAPI by passing `deprecated=True` to the
   FastAPI route decorator.
2. Add a docstring note that includes:
   - the version it was deprecated in
   - the version it is scheduled for removal in (default: **5 minor releases** later)
3. Do **not** use `openhands.sdk.utils.deprecation.deprecated` for FastAPI routes.
   That decorator affects Python warnings/docstrings, not OpenAPI, and may be a
   no-op before the declared deprecation version.

Example:

```py
@router.post("/foo", deprecated=True)
async def foo():
    """Do something.

    Deprecated since v1.2.3 and scheduled for removal in v1.7.0.
    """
```

That exact sentence shape is what the CI checks look for, so keep the wording
close to the example above.

### Deprecating a REST contract change

If an existing endpoint's request or response schema needs an incompatible change:

1. Do **not** replace the old contract in place without a migration path.
2. Add a deprecation notice for the old contract in the endpoint documentation and
   release notes, including the deprecated-in version and the removal target.
3. Keep the old contract available for **5 minor releases** while clients migrate.
   Prefer additive schema changes, parallel fields, or a versioned endpoint or
   versioned contract during the runway.
4. Only remove the old contract or make the incompatible shape mandatory after the
   runway has elapsed.

### Removing an endpoint or legacy contract

Removing an endpoint or a previously supported REST contract is a breaking change.

- Endpoints and legacy contracts must have a deprecation notice for **5 minor
  releases** before removal.
- Any release that introduces an allowed breaking REST API change should be
  at least a **MINOR** SemVer bump, after a 5-minor-release deprecation runway.

### CI enforcement

The workflow `Agent server REST API breakage checks` compares the current OpenAPI
schema against the previous `openhands-agent-server` release selected from PyPI,
but generates the baseline schema from the matching git tag under the current
workspace dependency set before diffing with [oasdiff](https://github.com/oasdiff/oasdiff).

It currently enforces:
- FastAPI route handlers must not use `openhands.sdk.utils.deprecation.deprecated`.
- Endpoints that document deprecation in their OpenAPI description must also set
  `deprecated: true`.
- Removed operations must already be marked `deprecated: true` in the previous
  release and must have reached the scheduled removal version documented in the
  baseline OpenAPI description.
- The recognized removal note uses the same wording as the deprecation checks,
  for example: `Deprecated since v1.14.0 and scheduled for removal in v1.19.0.`
- Other breaking REST contract changes fail the check; the replacement must ship
  additively or behind a versioned contract until the 5-minor-release runway has
  elapsed.
- The CI check enforces the deprecation runway, not release-wide SemVer policy.
  Whether a release also needs a MINOR bump still depends on the full scope of
  changes in that release.

Some contract-level migration-path details still rely on human review because
OpenAPI automation cannot fully infer every compatible rollout strategy.

WebSocket/SSE endpoints are not covered by this policy (OpenAPI only).
