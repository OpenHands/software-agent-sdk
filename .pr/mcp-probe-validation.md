# MCP HTTP rejection diagnostics: runtime validation

## Runtime

- Windows, Python 3.13.5, MCP 1.28.1, FastMCP 3.2.0, HTTPX 0.28.1.
- Original revision: `23d623b958f2703bcf238b4037dd7686e094d96a`.
- The endpoint is a real local TCP HTTP server returning status 403 and the
  JSON body `{"message": "403 Forbidden - MCP server not enabled"}`. It consumes
  the incoming request body before replying and closes during fixture teardown.
- The live regression sends an HTTP request to an actual uvicorn agent-server
  at `/api/mcp/test`. The SDK connects through the normal MCP transports.
- This reproduces GitLab-style rejection diagnostics. It does not reproduce a
  GitLab OAuth account, the Canvas UI, or every `Session terminated` scenario
  reported in issue #5276.
- Dependency versions and `uv.lock` are unchanged. LiteLLM 1.93.0 uses its
  Python fallback locally because its optional native extension is unavailable
  on this Windows setup. This MCP path does not use the native LLM extension.

## Before and after

```sh
uv run pytest tests/sdk/mcp/test_create_mcp_tool.py tests/agent_server/test_mcp_router.py tests/cross/test_remote_conversation_live_server.py -k 'preserves_http_error_body or surfaces_http_error_status or mcp_probe_reports_rejection_reason' -q --disable-warnings --timeout=45
```

Run with the original production files and the new regression tests:

```text
7 failed, 80 deselected, 1 warning in 16.12s
```

The SDK's HTTP error response body is unreadable after the stream closes. The
probe logs only:

```text
HTTP 403 from MCP server: request refused - check the server-side permission settings
```

Run with the updated production files:

```text
7 passed, 80 deselected, 1 warning in 15.80s
```

The four public SDK cases retain a readable HTTP 403 response body: HTTP and SSE,
each for ordinary and package-declared MCP servers. Both router cases and the
live HTTP test assert `ok=false`, `error_kind=unknown`, HTTP 403, and the server's
actual `not enabled` reason. The body assertion is not replaced with a generic
permission hint.

## Broader checks

```sh
uv run pytest tests/sdk/mcp tests/sdk/plugin/test_agent_plugins_mcp.py tests/agent_server/test_mcp_router.py tests/agent_server/test_mcp_oauth_store.py tests/cross/test_remote_conversation_live_server.py -q --disable-warnings --timeout=60
```

```text
1 failed, 296 passed, 4 skipped, 3 warnings in 225.89s
```

The sole failure is the existing
`test_websocket_attach_wait_does_not_block_ready_endpoint`: it measured
`/ready` at 0.912 seconds against a 0.5-second limit. This test does not configure
MCP, and its measured interval includes creating an HTTPX client. No timing or
WebSocket code is changed.

An isolated run against the original production files passes in 22.92 seconds.
The updated production files pass this test alongside all seven MCP regressions:

```sh
uv run pytest tests/sdk/mcp/test_create_mcp_tool.py tests/agent_server/test_mcp_router.py tests/cross/test_remote_conversation_live_server.py -k 'preserves_http_error_body or surfaces_http_error_status or mcp_probe_reports_rejection_reason or websocket_attach_wait_does_not_block_ready_endpoint' -q --disable-warnings --timeout=60
```

```text
8 passed, 79 deselected, 1 warning in 25.55s
```

All changed Python files pass Ruff lint and format checks, pycodestyle, Pyright,
import dependency rules, and tool registration checks. The SDK-wide forbidden
dynamic-attribute checker passes when supplied repository-relative POSIX paths;
automatic discovery uses Windows backslashes while its baseline keys use `/`.
The checker and baseline are not changed or disabled.

Local pytest runs use `UV_NO_SYNC=1`, `PYTHONUTF8=1`,
`LITELLM_LOCAL_MODEL_COST_MAP=True`, and `OPENHANDS_SUPPRESS_BANNER=1` after
installing the locked dependencies. GitHub CI remains the production Linux
environment check.
