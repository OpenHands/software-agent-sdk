# PR #4013 live evidence

Date: 2026-07-10 UTC

Branch: `fix-settings-mcp-schema-migration`

Initial evidence head: `fa22a8f5`

Base: `main` at `91f8b9403c16c30edee2f86cff634c38234c8a27`

## PR inspection

- Changed areas: SDK persisted settings migration, agent-server event transport,
  focused tests/fixtures for those paths, test hardening for CI isolation, and
  temporary `.pr/` evidence artifacts.
- Review threads: GitHub GraphQL returned no review threads.
- Checks: `gh pr checks 4013 --repo OpenHands/software-agent-sdk` reports all
  current checks passing, with only `cleanup-on-approval` skipped.

## PR description check

The stale failed run was inspected with:

```bash
gh run view 29071297471 --repo OpenHands/software-agent-sdk --job 86293164010 --log
```

The failure at 2026-07-10 05:25 UTC reported that the PR body did not contain
the required visible `HUMAN:`, `AGENT:`, `## Why`, and `## How to Test`
markers at that time.

The latest validator run was inspected with:

```bash
gh run view 29072920527 --repo OpenHands/software-agent-sdk --job 86298085498 --log
```

It passed at 2026-07-10 06:05 UTC with `PR description validation passed.`

The current live PR body was also validated locally through the repository
checker:

```bash
python .github/scripts/check_pr_description.py --body-file <(gh pr view 4013 --repo OpenHands/software-agent-sdk --json body --jq .body)
```

Result:

```text
PR description validation passed.
```

The `HUMAN:` content was not edited by the agent.

## Local validation

Repository setup:

```bash
make build
```

Result: dependency sync and pre-commit hook installation completed successfully.

Focused test suite:

```bash
uv run pytest -q tests/sdk/test_settings.py tests/cross/test_check_persisted_settings_compat.py tests/agent_server/test_event_router.py tests/agent_server/test_event_router_websocket.py
```

Result:

```text
173 passed, 5 warnings in 1.49s
```

Persisted settings compatibility:

```bash
uv run python .github/scripts/check_persisted_settings_compat.py
```

Result:

```text
Validated 9 persisted settings fixture(s) under tests/sdk/persisted_settings_baselines
Validated 7 baseline payload(s) from PyPI release openhands-sdk==1.32.0, openhands-agent-server==1.32.0
```

Evidence script pre-commit:

```bash
uv run pre-commit run --files .pr/live_settings_migration_check.py
```

Result: Ruff format, Ruff lint, pycodestyle, pyright, import dependency rules,
and tool subclass registration all passed.

CI-failure hardening after first evidence push:

```bash
uv run pytest -q tests/agent_server/test_conversation_service.py::TestConversationTreeForkAndNavigate::test_fork_from_event_slices_branch_and_records_lineage tests/agent_server/test_sockets_service_getters.py::test_events_socket_uses_app_state_conversation_service tests/agent_server/test_sockets_service_getters.py::test_bash_events_socket_uses_app_state_bash_event_service
```

Result:

```text
3 passed, 5 warnings in 0.48s
```

```bash
uv run pre-commit run --files tests/agent_server/test_conversation_service.py tests/agent_server/test_sockets_service_getters.py
```

Result: Ruff format, Ruff lint, pycodestyle, pyright, import dependency rules,
and tool subclass registration all passed.

Local reproduction of the CI agent-server mode:

```bash
CI=true uv run python -m pytest -q -n auto -x tests/agent_server
```

Result:

```text
1436 passed, 56 warnings in 112.02s (0:01:52)
```

## Live-code verification

Command:

```bash
OPENHANDS_SUPPRESS_BANNER=1 uv run python .pr/live_settings_migration_check.py
```

Result:

```json
{
  "current_wrapper_rejection": {
    "error_count": 1,
    "first_error_type": "extra_forbidden",
    "rejected": true
  },
  "event_transport": {
    "search_parent_id": "parent_event",
    "search_tool_kinds": [
      "FinishTool",
      "VisionInspectTool"
    ],
    "websocket_parent_id": "websocket_parent",
    "websocket_tool_kinds": [
      "FinishTool"
    ]
  },
  "settings_migration": {
    "has_mcpServers_wrapper": false,
    "mcp_server_names": [
      "superhuman-mail"
    ],
    "oauth_access_token": "token-value",
    "oauth_client_id": "client-id",
    "oauth_strategy": "oauth2",
    "schema_version": 5
  }
}
```

This exercises the changed code paths directly:

- A schema v4 MCP OAuth payload migrates to schema v5.
- The persisted MCP shape is the SDK-native server map, not the legacy
  `mcpServers` wrapper.
- Current-version legacy MCP wrappers are rejected instead of being silently
  normalized.
- Event search/WebSocket transport preserves branch `parent_id` data, and event
  search preserves the newer `VisionInspectTool` kind instead of filtering it.

## Remaining blocker

`gh pr view` reports `reviewDecision: REVIEW_REQUIRED`. No unresolved review
threads remain, but a human review/approval is still required before merge.
