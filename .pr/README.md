# PR #2409 — Integration Test Report

## Test: `workspace.get_llm()` and `workspace.get_secrets()` against staging

**Date:** 2026-03-16
**Target:** `https://ohpr-13383-240.staging.all-hands.dev` (deploy PR [#3436](https://github.com/OpenHands/deploy/pull/3436))
**Server PR:** [OpenHands/OpenHands#13383](https://github.com/OpenHands/OpenHands/pull/13383) (companion)
**Script:** `examples/02_remote_agent_server/09_cloud_workspace_saas_credentials.py`

## Results

| Step | Status | Details |
|---|---|---|
| Sandbox provisioning | ✅ | `6vCNUFNBqGOQlgCyPar4yZ`, RUNNING in ~60s |
| `workspace.get_llm()` | ✅ | Retrieved `litellm_proxy/minimax-m2.5` + api_key + base_url from SaaS |
| `workspace.get_secrets()` | ✅ | Returned `[]` (correct — no secrets configured on staging account) |
| Conversation execution | ✅ | Agent ran task, 17 events, 39.2s |
| Sandbox cleanup | ✅ | Deleted successfully (after fix — see below) |

## Bug fix included: sandbox DELETE 405

During testing, discovered the `cleanup()` method was calling
`DELETE /api/v1/sandboxes?sandbox_id=X` (query param on collection route), which
returned 405 Method Not Allowed.

The server's delete endpoint is `DELETE /api/v1/sandboxes/{id}` with a required
`sandbox_id` query parameter (a FastAPI routing quirk where the path variable
name `{id}` doesn't match the function parameter name `sandbox_id`).

**Fix:** Changed to `DELETE /api/v1/sandboxes/{sandbox_id}?sandbox_id={sandbox_id}`.

## Logs

- `logs/run_stdout.log` — stdout (agent conversation output)
- `logs/run_stderr.log` — stderr (SDK lifecycle logs, no errors)
