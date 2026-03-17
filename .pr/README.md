# PR #2409 — Integration Test Report

## Test: `workspace.get_llm()` and `workspace.get_secrets()` against staging

**Date:** 2026-03-16 (initial), **2026-03-17** (provider tokens e2e)
**Target:** `https://ohpr-13383-240.staging.all-hands.dev` (deploy PR [#3436](https://github.com/OpenHands/deploy/pull/3436))
**Server PR:** [OpenHands/OpenHands#13383](https://github.com/OpenHands/OpenHands/pull/13383) (companion)
**Server commit:** `c0ed13f374ec647bf5349984961823650c8928c8` (includes `get_provider_tokens(as_env_vars=True)` refactor)
**SaaS server:** Custom build from [OpenHands/OpenHands#13383](https://github.com/OpenHands/OpenHands/pull/13383) (provides `/sandboxes/{id}/settings/secrets` endpoints)
**Sandbox agent-server:** `ghcr.io/openhands/agent-server:1.13.0-python` (stock — no SDK PR changes needed in sandbox)

## Provider Tokens E2E (2026-03-17) ✅

Full e2e run against staging with updated server commit `c0ed13f` that exposes provider tokens (e.g. `github_token`) via the sandbox secrets endpoint.

| Phase | Status | Details |
|---|---|---|
| `workspace.get_llm()` | ✅ | `model=litellm_proxy/minimax-m2.5`, api_key + base_url inherited from SaaS |
| `workspace.get_secrets()` | ✅ | Returns **3 secrets**: `['secret_1', 'secret_2', 'github_token']` |
| Provider token discovery | ✅ | `github_token` included — **provider tokens now flow to SDK** |
| `LookupSecret` resolution | ✅ | All 3 secrets resolved: `secret_1` (len=5), `secret_2` (len=5), `github_token` (len=40) |
| Sandbox cleanup | ✅ | Sandbox `5vwSsTFHIIjxie4pvjN3fQ` created + deleted cleanly |

> Log: [`.pr/logs/e2e_provider_tokens.log`](logs/e2e_provider_tokens.log)

## Initial Results (2026-03-16) ✅

| Component | Status | Details |
|---|---|---|
| Sandbox provisioning | ✅ | Created, RUNNING in ~50s, cleaned up on exit |
| `workspace.get_llm()` | ✅ | Retrieves `litellm_proxy/minimax-m2.5` + api_key + base_url from SaaS |
| `workspace.get_secrets()` | ✅ | Discovers `['DUMMY_1', 'DUMMY_2']` via `GET /sandboxes/{id}/settings/secrets` |
| `update_secrets(LookupSecret)` | ✅ | LookupSecret with session key in `headers` survives serialization |
| Env vars exported inside sandbox | ✅ | `_export_envs` resolves LookupSecret → secrets appear as real env vars |
| Sandbox cleanup | ✅ | `DELETE /api/v1/sandboxes/{id}` succeeds |

> Log: [`.pr/logs/run_combined.log`](logs/run_combined.log)

### Agent verification output (from inside the sandbox)

The agent ran the exact Python command we gave it. The output proves the secrets
were resolved by the `_export_envs` pipeline and exported as real env vars:

```
$ python3 -c "import os; v=os.environ.get('DUMMY_1',''); print(f'DUMMY_1: len={len(v)}, last_half={v[len(v)//2:]}')" && \
  python3 -c "import os; v=os.environ.get('DUMMY_2',''); print(f'DUMMY_2: len={len(v)}, last_half={v[len(v)//2:]}')"

DUMMY_1: len=14, last_half=ecret 1
DUMMY_2: len=14, last_half=ecret 2
```

Both secrets are 14 characters long, non-empty, and the second half matches
the expected values (`"Dummy secret 1"` → `"ecret 1"`, `"dummy secret 2"` → `"ecret 2"`).

## Issues found and fixed during testing

### 1. Sandbox DELETE 405
`cleanup()` called `DELETE /api/v1/sandboxes?sandbox_id=X` (query param on collection route) → 405.
**Fix:** Changed to `DELETE /api/v1/sandboxes/{sandbox_id}?sandbox_id={sandbox_id}`.

### 2. SecretStr serialization redacts headers
`SecretSource.model_dump(mode="json")` redacts `SecretStr` fields (returns `**********`).
`LookupSecret.headers` containing `X-Session-API-Key` was lost during `update_secrets()` serialization.
**Fix:** Added `context={"expose_secrets": True}` to `model_dump()` in `RemoteConversation.update_secrets()`.

### 3. Removed `env_headers` (was unnecessary complexity)
Original design added `env_headers` field to `LookupSecret` so session key VALUE never appeared
in serialized JSON (only the env var NAME). This created a deployment dependency — the agent-server
image needed the new field, but the stock image didn't have it.
**Fix:** Dropped `env_headers` entirely. Using the existing `headers` field with `expose_secrets`
context is simpler and works with the stock agent-server image. No custom build or redeploy needed.
Net: **-35 lines, +10 lines**.
