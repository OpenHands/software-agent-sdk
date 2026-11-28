# Navigation during an active run: verification

Verified code tree: `4fc06ef7ec6822e6928b6e6bfc792af28e281f98` (2026-11-19).

This record supersedes the old client follow-up and source-only evidence notes in the PR description. The GitHub integration returned 403 for updating that description or posting a follow-up comment, so the current record is included in the PR branch.


## Why and changes

A live async run releases the conversation state lock during LLM I/O. Navigation in that window could move HEAD before the assistant reply was appended, persisting the reply on the wrong branch (#6582).

The local conversation rejects navigation during an active async run, and the Agent Server maps it through the existing typed `Conflict` response (409). Remote Python navigation now raises `RemoteConversationRunInProgressError`, which is both `ConversationRunInProgressError` and `httpx.HTTPStatusError`, preserving its request and response.

Two live-server regressions cover navigation to an earlier event and to the empty HEAD. They start a real async agent run, pause at deterministic LLM I/O, exercise the REST and WebSocket clients, read the persisted ancestry through a fresh EventLog, and confirm idle navigation still succeeds afterward. Two TypeScript tests cover both navigation entry points: 409 propagates without refreshing cached state. The shared HttpClient already handles the status; the generated schema remains pinned to the released server by its generation workflow.

This head integrates upstream main at `141423921fdaeb08cd5f53464a69e860da70b66f` without rewriting published commits.

## Validation on the new tree

Python 3.13.5, `uv sync --frozen --dev`:

```sh
LITELLM_LOCAL_MODEL_COST_MAP=True uv run --frozen pytest \
  tests/sdk/conversation/local/test_conversation_tree.py \
  tests/sdk/conversation/remote/test_remote_conversation.py \
  tests/agent_server/test_conversation_router.py \
  tests/cross/test_no_http_exception_outside_api.py \
  tests/cross/test_remote_conversation_live_server.py \
  -k 'not gateway and not child and not hooks and not stop_hook' --timeout=90
```

**285 passed, 1 skipped, 10 deselected.** The environment setting avoids an observed LiteLLM background remote-catalog import deadlock during collection. The test substitutes deterministic LLM responses; the server, sockets, async run and on-disk event log are real.

Before/after evidence:
- Temporarily removing the navigation guard makes both live cases fail: navigation incorrectly returns 200 instead of 409.
- Before the remote error mapping, the live workflow fails because Python exposes only a plain HTTPStatusError.
- Restoring the fixes passes both live cases and preserves the active user turn in the persisted reply's ancestry.

Additional checks: TypeScript API-client suite **105 passed**; TypeScript build passed; ESLint had 0 errors and 5 existing warnings. Pre-commit checks, including Pyright, pass for the changed Python implementation and live tests; focused Ruff and `git diff --check` pass. Full read-only Ruff checks were attempted: 66 findings were also present on upstream main, so full-repository lint is not claimed green.

## Limits

The full repository test suite, Docker/GPU tests and external-model evaluations were not run. The live server shares the test process and uses a deterministic LLM response. Upstream Actions authorization and maintainer review remain separate.

Fixes #6582.
