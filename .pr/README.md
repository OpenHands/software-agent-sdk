# PR #2409 — Integration Test Report

## Test: `workspace.get_llm()` and `workspace.get_secrets()` against staging

**Date:** 2026-03-16
**Target:** `https://ohpr-13383-240.staging.all-hands.dev` (deploy PR [#3436](https://github.com/OpenHands/deploy/pull/3436))
**Server PR:** [OpenHands/OpenHands#13383](https://github.com/OpenHands/OpenHands/pull/13383) (companion)
**Script:** `examples/02_remote_agent_server/09_cloud_workspace_saas_credentials.py`
**Agent-server image:** `ghcr.io/openhands/agent-server:1.13.0-python` (stock, no custom build)

## Final Results (all passing ✅)

| Component | Status | Details |
|---|---|---|
| Sandbox provisioning | ✅ | Created, RUNNING in ~50s, cleaned up on exit |
| `workspace.get_llm()` | ✅ | Retrieves `litellm_proxy/minimax-m2.5` + api_key + base_url from SaaS |
| `workspace.get_secrets()` | ✅ | Discovers `['DUMMY_1', 'DUMMY_2']` via `GET /sandboxes/{id}/settings/secrets` |
| LookupSecret HTTP resolution (SDK client) | ✅ | Direct HTTP GET resolves values correctly using session key in `headers` |
| LookupSecret → env var injection (`_export_envs`) | ✅ | Agent sees `$DUMMY_1` / `$DUMMY_2` as real env vars (masked in output, verified via Python) |
| Sandbox cleanup | ✅ | `DELETE /api/v1/sandboxes/{id}` succeeds |

### Agent verification output

```
$ echo "DUMMY_1: $DUMMY_1"
DUMMY_1: <secret-hidden>        ← value IS set, masked by terminal

# Python extraction (bypasses masking):
DUMMY_1 last 50%: "ecret 1"    ← correct (original: "Dummy secret 1")
DUMMY_2 last 50%: "ecret 2"    ← correct (original: "dummy secret 2")
```

## Issues found and fixed during testing

### 1. Sandbox DELETE 405
`cleanup()` called `DELETE /api/v1/sandboxes?sandbox_id=X` (query param on collection route) → 405.
**Fix:** Changed to `DELETE /api/v1/sandboxes/{sandbox_id}?sandbox_id={sandbox_id}`.

### 2. SecretStr serialization redacts headers
`SecretSource.model_dump(mode="json")` redacts `SecretStr` fields (returns `**********`).
`LookupSecret.headers` containing `X-Session-API-Key` was lost during `update_secrets()` serialization.
**Fix:** Added `context={"expose_secrets": True}` to `model_dump()` in `RemoteConversation.update_secrets()`.

### 3. Removed `env_headers` (was unnecessary complexity)
Original design added `env_headers` field to `LookupSecret` so session key VALUE never appeared in serialized JSON (only the env var NAME). This created a deployment dependency — the agent-server image needed the new field.
**Fix:** Dropped `env_headers` entirely. Using the existing `headers` field with `expose_secrets` context is simpler and works with the stock agent-server image. No custom build or redeploy needed. Net: **-35 lines, +10 lines**.
