# PR #2409 — Integration Test Report

## Test: `workspace.get_llm()` and `workspace.get_secrets()` against staging

**Date:** 2026-03-16
**Target:** `https://ohpr-13383-240.staging.all-hands.dev` (deploy PR [#3436](https://github.com/OpenHands/deploy/pull/3436))
**Server PR:** [OpenHands/OpenHands#13383](https://github.com/OpenHands/OpenHands/pull/13383) (companion)
**Script:** `examples/02_remote_agent_server/09_cloud_workspace_saas_credentials.py`

## Results Summary

| Component | Status | Details |
|---|---|---|
| `workspace.get_llm()` | ✅ | Retrieves `litellm_proxy/minimax-m2.5` + api_key + base_url from SaaS |
| `workspace.get_secrets()` | ✅ | Discovers `['DUMMY_1', 'DUMMY_2']` secret names |
| LookupSecret HTTP resolution (SDK client) | ✅ | Direct HTTP GET to secrets endpoint resolves values correctly |
| LookupSecret HTTP resolution (from inside sandbox) | ✅ | Agent's Python code successfully resolves secrets via HTTP with SESSION_API_KEY |
| Env var injection (StaticSecret/plain strings) | ✅ | `_export_envs` pipeline works — agent sees `$DUMMY_1` and `$DUMMY_2` as env vars |
| Env var injection (LookupSecret with env_headers) | ⚠️ | Requires agent-server image with SDK PR #2409 (see below) |
| Sandbox cleanup | ✅ | Deleted successfully (after fix) |

## Deployment Dependency: LookupSecret `env_headers`

The `env_headers` field on `LookupSecret` is **new in this PR**. The current agent-server
image (`1.13.0-python`) does not have it. When the SDK client sends a LookupSecret with
`env_headers` to the agent-server, Pydantic silently drops the unknown field, so
`get_value()` makes HTTP requests without the `X-Session-API-Key` header and fails with 401.

**To resolve:** The SDK PR must be merged first, then a new agent-server image built. The
staging deployment's sandbox spec must then reference the updated image.

The workaround for testing was to eagerly resolve secret values on the SDK client side and
send them as plain strings (which the agent-server converts to `StaticSecret`). This proved
the `_export_envs` → `get_secrets_as_env_vars` pipeline works correctly end-to-end.

## Additional finding: SecretStr serialization redacts values

`SecretSource.model_dump(mode="json")` redacts `SecretStr` fields (returns `**********`).
This means passing `StaticSecret` objects through `RemoteConversation.update_secrets()` also
fails because the value is redacted during JSON serialization. Plain string values bypass this
issue because they're not wrapped in `SecretStr` until the server side.

## Bug fixes included

1. **Sandbox DELETE 405:** Changed to `DELETE /api/v1/sandboxes/{sandbox_id}?sandbox_id={sandbox_id}`
2. **LookupSecret serialization:** Added `model_dump(mode="json")` call in `update_secrets()`

## Logs

- `logs/run_stdout.log` — stdout (agent conversation output)
- `logs/run_stderr.log` — stderr (SDK lifecycle logs, no errors)
