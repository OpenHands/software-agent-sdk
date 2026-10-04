# Historical regression evidence for #4588 / PR #4595

Verified on macOS arm64, CPython 3.13.11, on 2026-10-04. Every historical
checkout used its own frozen `uv.lock` and unmodified production code. Only
`budgets.py`, `test_historical_regressions.py`, and `test_lifecycle_isolation.py`
were copied from this PR. The historical fixtures and helpers were retained.
The passing production tree is current `main` at
`b347047e2dcdd8f4b2be810aa8bc6632bc756d41`; the PR has no production-code diff
against that tree.

## Regression matrix

All node IDs below are under `tests/agent_server/stress/`. Each pre-fix SHA is
the first parent of the corresponding fix's merge commit.

| Regression | Test node | Pre-fix production commit | Actual pre-fix result | Current-main result |
|---|---|---|---|---|
| #4570 / #4514: global lifecycle lock | `test_lifecycle_isolation.py` | `611629e6999ad398dc49c761721eefe18d5e6bd9` | **1 failed**, 5.20 s: unrelated create/load/delete blocked behind the stuck close | **PASSED** |
| #4481: blocking bash event search | `test_historical_regressions.py::test_bash_search_keeps_health_responsive` | `20f7f5f8f1e1a996777f9d78bd1a0145b3348c57` | **1 failed**, 0.66 s: scheduled health probe took **0.507 s**, exceeding **0.150 s** | **PASSED** |
| #4417: blocking ConversationInfo composition | `test_historical_regressions.py::test_conversation_listing_keeps_health_responsive` | `234e4cc79eda6a1a8ea56eddcc26eb44d75a9d20` | **1 failed**, 0.75 s: scheduled health probe took **0.509 s**, exceeding **0.150 s** | **PASSED** |
| #4473: LLM global-config serialization | `test_historical_regressions.py::test_real_llm_calls_overlap_across_conversations` | `5bfa7fc5398649cacf4031d477cc47d754c49078` | **1 failed**, 1.12 s: all four conversations finished, but peak simultaneous provider transports was **1** | **PASSED**, provider transports overlap |

The complete gate command on current main's production code returned
**17 passed in 61.65 s**. The four focused regression scenarios returned
**4 passed in 1.67 s**.

## Commands

From the PR checkout, set `PR_DIR` to its absolute path. Repeat the following
for each matrix row, using that row's `NUMBER`, `PRE_FIX`, and `NODE`:

```bash
PR_DIR="$(pwd)"
NUMBER=4481
PRE_FIX=20f7f5f8f1e1a996777f9d78bd1a0145b3348c57
NODE=tests/agent_server/stress/test_historical_regressions.py::test_bash_search_keeps_health_responsive

git worktree add --detach "../sdk-pre-$NUMBER" "$PRE_FIX"
cp "$PR_DIR"/tests/agent_server/stress/{budgets.py,test_historical_regressions.py,test_lifecycle_isolation.py} \
  "../sdk-pre-$NUMBER/tests/agent_server/stress/"
cd "../sdk-pre-$NUMBER"
uv sync --frozen --group dev
CI=true uv run --frozen python -m pytest -q -m stress "$NODE" --no-cov
```

Each historical invocation must exit **1**, with the assertion in the matrix,
not an import, setup, or credential error. The same node and command pass on
the current PR checkout, whose production code matches the pinned main SHA.

The full current-main gate command executed was:

```bash
CI=true uv run python -m pytest -vvs \
  -m stress --durations=10 tests/agent_server/stress
```

The focused current-main command executed was:

```bash
uv run pytest -q -m stress \
  tests/agent_server/stress/test_historical_regressions.py \
  tests/agent_server/stress/test_lifecycle_isolation.py --no-cov
```

## What the new scenarios exercise

- Bash search goes through the real HTTP router and persisted event store.
  The directory scan has a controlled 0.5 s delay. Both old glob-based search
  and current scandir-based search encounter that same filesystem boundary.
- Conversation search goes through the real HTTP router and real persisted
  conversation. Snapshot composition has the same controlled 0.5 s delay.
- Independent conversations run via the real HTTP run endpoint, Agent, and
  `LLM` transport path. Only the external LiteLLM provider call is replaced
  with a credential-free, 0.2 s response. The test does **not** use TestLLM,
  which would bypass the global-config transport lock being tested.
- The existing lifecycle scenario stalls an actual EventService close through
  a subscriber and requires unrelated operations to complete within 5 s.

The health canaries measure scheduling delay plus HTTP service time. Measuring
only an in-process ASGI request's duration would miss a blocked event loop: the
request would start after the blocking work had already ended. The existing
150 ms ceiling is unchanged; the deliberate 500 ms load makes the historical
failure clear while leaving over 100 ms for CI scheduling noise. All temporary
probe tasks and subscribers are joined or released on exit.

This uses the repository's in-process ASGI harness, not a deployed server,
external model, or reproduction of a specific production filesystem/GC
incident. It proves the scheduling and cross-conversation isolation invariants
at the real affected boundaries without paid credentials.
