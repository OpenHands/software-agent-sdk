# MCP client and tools

A Python program gives an agent Model Context Protocol servers with `Agent(mcp_config={"name": MCPServer(...)})`: a local server started as a subprocess (`command`, `args`, `env`, `cwd`; stdio transport), or a remote one reached over streamable HTTP or SSE (`url`, `transport`, `headers`, `auth`). When the conversation initializes the agent, the SDK opens one FastMCP client for all enabled servers on a background event loop, lists their tools, and turns each MCP tool into an `MCPToolDefinition` whose JSON schema becomes the model-facing tool (with `summary` and, when a security analyzer is set, `security_risk` added). Tool names are the MCP names, prefixed with `<server>_` when there are several servers. A call validates the model's arguments against the schema, expands `$VAR` secret references from the conversation, sends `tools/call` over the same session, and returns an `MCPToolObservation` with text and images (secrets masked, errors as `is_error`). The session survives between calls, is reconnected once if it drops, and follows `notifications/tools/list_changed`. Remote servers can authenticate with a bearer token, API key, basic auth, custom header or OAuth 2 (dynamic client registration and token refresh against the discovered token endpoint). A server that fails to start is logged and skipped; the conversation continues without its tools.

Source: `openhands-sdk/openhands/sdk/mcp/`, `openhands-sdk/openhands/sdk/agent/base.py`, `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`, `openhands-sdk/openhands/sdk/skills/utils.py` (MCP variable expansion)

Needs: `llm` (examples), `uvx` and `node` (example 07's `fetch` and `repomix` servers), `network` (example 07 reads GitHub, example 08 reaches Notion)

Examples: `examples/01_standalone_sdk/07_mcp_integration.py`, `examples/01_standalone_sdk/08_mcp_with_oauth.py`

## Sub-features

- `S09.config-model`: `MCPServer` forbids unknown fields, requires `command` for stdio and `url` for remote transports, refuses `auth` together with an explicit `Authorization` header, and `coerce_mcp_config` also accepts the external `{"mcpServers": {...}}` shape while `create_mcp_tools` rejects it. Unit: `tests/sdk/mcp/test_create_mcp_tool.py` (`test_create_mcp_tools_rejects_external_config_shapes`). Example: none. Real-LLM: none.
- `S09.config-secrets`: `env`, `headers` and credential values are `SecretStr`, redacted in dumps unless a cipher is in context, round-trip through encrypted JSON, and never appear in persisted conversation state or state-update events. Unit: `tests/sdk/mcp/test_mcp_config_secrets.py`, `tests/sdk/conversation/test_mcp_secrets_serialization_leak.py`. Example: none. Real-LLM: none.
- `S09.stdio-server`: a `command`/`args` server is started as a subprocess and its tools are listed with their input schemas. Unit: `tests/sdk/mcp/test_create_mcp_tool.py::test_create_mcp_tools_stdio_server` (spawns `tests/sdk/mcp/stdio_test_server.py`). Example: `examples/01_standalone_sdk/07_mcp_integration.py`. Real-LLM: none.
- `S09.http-sse-server`: `url` servers connect over streamable HTTP or SSE (transport inferred from a `/sse` URL when omitted), tools from several servers carry `<server>_` prefixes, and calls return the server's result. Unit: `tests/sdk/mcp/test_create_mcp_tool.py` (`test_create_mcp_tools_http_server`, `test_create_mcp_tools_sse_server`, `test_create_mcp_tools_mixed_servers`, `test_create_mcp_tools_transport_inferred_from_url`, `test_execute_http_tool`, `test_execute_sse_tool`). Example: none. Real-LLM: none.
- `S09.static-auth`: `auth` strategies `none`, `bearer`, `api_key` (custom `header_name`), `basic` and `header` turn into the right HTTP headers. Unit: `tests/sdk/mcp/test_create_mcp_tool.py::test_mcp_auth_credentials_to_http_headers`. Example: none. Real-LLM: none.
- `S09.oauth`: `auth=MCPOAuthAuthCredential(strategy="oauth2", authentication=MCPOAuthAuthentication(...))` builds a FastMCP OAuth client with the given scopes, client id/secret or metadata URL and an optional token store or factory; an expired token is refreshed against the token endpoint discovered from server metadata, falling back to `<origin>/token`, and a valid token is sent without discovery. Unit: `tests/sdk/mcp/test_oauth.py`, `tests/sdk/mcp/test_create_mcp_tool.py` (`test_prepare_mcp_config_*oauth*`). Example: `examples/01_standalone_sdk/08_mcp_with_oauth.py`. Real-LLM: none.
- `S09.enabled-flag`: `MCPServer(enabled=False)` keeps a server configured but never connects to it; if every server is disabled `create_mcp_tools` raises and the conversation skips MCP entirely. Unit: `tests/sdk/mcp/test_create_mcp_tool.py` (`test_create_mcp_tools_skips_disabled_servers`, `test_create_mcp_tools_all_servers_disabled`), `tests/sdk/conversation/test_local_conversation_mcp.py::test_disabling_every_server_skips_the_mcp_connection`. Example: none. Real-LLM: none.
- `S09.listing-timeout`: tool listing that exceeds `timeout` (30 s in a conversation) raises `MCPTimeoutError` naming the servers and closes the client; an unreachable server raises. Unit: `tests/sdk/mcp/test_create_mcp_tool.py` (`test_create_mcp_tools_timeout_error_message`, `test_create_mcp_tools_connection_to_nonexistent_server`). Example: none. Real-LLM: none.
- `S09.startup-failure-tolerated`: when MCP startup fails inside a `LocalConversation`, a warning is logged and the conversation runs with its other tools. Unit: `tests/sdk/conversation/test_local_conversation_mcp.py::test_mcp_startup_failure_does_not_abort_conversation`. Example: none. Real-LLM: none.
- `S09.schema-conversion`: an MCP `inputSchema` becomes the tool's OpenAI and Responses parameters with nested objects, arrays, required lists and descriptions preserved, plus `summary` and (with a security analyzer) `security_risk`; MCP annotations are kept. Unit: `tests/sdk/mcp/test_mcp_nested_schema.py`, `tests/sdk/mcp/test_mcp_security_risk.py`, `tests/sdk/mcp/test_mcp_tool.py`. Example: none. Real-LLM: none.
- `S09.argument-validation`: `action_from_arguments` validates the model's arguments against the schema, drops `None` values and the OpenHands `kind` discriminator before the call, keeps a schema property literally named `kind`, and raises on invalid input; `security_risk` and `summary` are removed by the agent before it calls `action_from_arguments`, which rejects them as extra inputs when given directly. Unit: `tests/sdk/mcp/test_mcp_tool_validation.py`, `tests/sdk/mcp/test_mcp_tool_kind_field.py`, `tests/sdk/mcp/test_mcp_action_serialization.py`. Example: none. Real-LLM: none.
- `S09.observation`: a `CallToolResult` becomes an `MCPToolObservation` with text and image content, `isError` mapped to `is_error`, structured JSON rendered as text; an exception or timeout (300 s per call) becomes an error observation. Unit: `tests/sdk/mcp/test_mcp_tool.py`, `tests/sdk/mcp/test_mcp_observation.py`. Example: none. Real-LLM: none.
- `S09.secret-refs-in-calls`: `$VAR`, `${VAR}` and `${VAR:-default}` in a call's arguments are expanded from the conversation's secret registry (never from the host environment), and secret values in the result text are masked. Unit: `tests/sdk/mcp/test_mcp_secret_expansion.py`. Example: none. Real-LLM: none.
- `S09.session-reuse`: all calls share one connected session, so server-side session state (counters, auth tokens) persists across calls and tools, and closing the client releases the connection. Unit: `tests/sdk/mcp/test_mcp_session_persistence.py`, `tests/sdk/mcp/test_stateful_mcp.py`. Example: none. Real-LLM: none.
- `S09.session-recovery`: if the session was lost (for example after an HTTP 503), the next call reconnects once and retries; a closed client or a failed reconnect returns an error observation instead of raising. Unit: `tests/sdk/mcp/test_mcp_tool.py` (`test_call_tool_reconnects_when_session_lost`, `test_call_tool_fails_when_client_closed`, `test_call_tool_fails_when_reconnect_fails`), `tests/sdk/mcp/test_create_mcp_tool.py::test_mcp_tool_reconnects_after_http_error`. Example: none. Real-LLM: none.
- `S09.tools-list-changed`: on `notifications/tools/list_changed` the client re-lists tools, reports only new ones to `on_tools_changed`, sends the full snapshot (additions, updates, removals) to `on_tools_reconciled`, and the agent registers them on its current instance; a custom `MCPToolProvider` without `on_tools_reconciled` still works. Unit: `tests/sdk/mcp/test_mcp_tool_list_changed.py`, `tests/sdk/conversation/test_local_conversation_mcp.py` (`test_reconciliation_targets_replaced_agent`, `test_legacy_provider_without_on_tools_reconciled_still_works`). Example: none. Real-LLM: none.
- `S09.filter-tools-regex`: `Agent(filter_tools_regex=...)` filters MCP and static tools by name while built-in tools stay. Unit: `tests/sdk/agent/test_filter_tools_regex.py`. Example: `examples/01_standalone_sdk/07_mcp_integration.py` (keeps only `repomix` `pack_codebase`). Real-LLM: none.
- `S09.config-var-expansion`: `${VAR}` and `${VAR:-default}` placeholders in `Agent.mcp_config` (and Claude Code plugin `.mcp.json`) are expanded from conversation secrets before servers start. Unit: `tests/sdk/skills/test_mcp_config_expansion.py`, `tests/sdk/conversation/test_local_conversation_plugins.py::TestPluginMcpSecretsExpansion`. Example: none. Real-LLM: none.
- `S09.tool-serialization`: `MCPToolDefinition` is frozen, serializes polymorphically with `kind`, is written in the MCP wire spelling (`inputSchema`) and loads either spelling, so conversations persisted under older `mcp` majors resume. Unit: `tests/sdk/mcp/test_mcp_tool_serialization.py`, `tests/sdk/mcp/test_mcp_tool_immutability.py`, `tests/sdk/mcp/test_mcp_wire_compat.py`. Example: none. Real-LLM: none.

## How to get to it (agent POV)

- `Agent(llm=..., tools=[...], mcp_config={"fetch": MCPServer(command="uvx", args=[...]), "remote": MCPServer(url="https://.../mcp", transport="http", headers={...}, auth=...)}, filter_tools_regex=...)`. `MCPServer`, `MCPOAuthAuthCredential`, `MCPOAuthAuthentication` import from `openhands.sdk.mcp`; the static credential classes (`MCPBearerAuthCredential`, `MCPApiKeyAuthCredential`, `MCPBasicAuthCredential`, `MCPHeaderAuthCredential`, `MCPNoneAuthCredential`) from `openhands.sdk.mcp.config`. Covers every sub-feature through a conversation.
- `LocalConversation(agent=..., mcp_tool_provider=MyProvider())` (not accepted by the `Conversation` factory) replaces how MCP clients are created; `DefaultMCPToolProvider` is the default. `S09.tools-list-changed`.
- Direct use without an agent: `with create_mcp_tools(mcp_config, timeout=30.0, on_tools_changed=..., on_tools_reconciled=..., mcp_oauth_token_storage=..., mcp_oauth_factory=...) as client: for tool in client.tools: ...`; `tool(action)` or `tool.action_from_arguments({...})`. Covers `S09.stdio-server`, `S09.http-sse-server`, `S09.listing-timeout`, `S09.argument-validation`.
- `conversation.update_secrets({...})` / `Conversation(secrets=...)` feed `S09.secret-refs-in-calls` and `S09.config-var-expansion`.
- Plugins add servers through `.mcp.json` or `mcp.json` (S08); sub-agents can carry their own `mcp_config` (S11).
- Env vars: none of its own; examples read `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL`.

## Driving it

Preconditions:
- `uv sync --dev` has run (provides `fastmcp` and `uvicorn`); `control-sdk` is on `PATH` (`export PATH="$PWD/.agents/skills/verify-sdk/scripts:$PATH"`). `sdk-map run` does both.
- Live bullets: `DEEPSEEK_API_KEY`; example 07 also needs `uvx`, `npx` and network.

Every server here is `programs/S09/mcp_server.py`, a small FastMCP server the programs start over stdio, streamable HTTP or SSE on a free local port (tools: `echo`, `add`, `getenv`, `reverse`, `counter`, `nested`, `kind_echo`, `fail`, `picture`, `structured`, `slow`, `headers`, `die`, `register_extra`, `remove_extra`; options for OAuth endpoints, a slow or failing start, and a transient 503). Conversation programs record each scripted LLM call's tool schemas (`recorder.py`).

- **An MCP server inside a conversation (`S09.stdio-server`, `S09.schema-conversion`, `S09.argument-validation`, `S09.observation`, `S09.secret-refs-in-calls`, `S09.session-reuse`, `S09.tools-list-changed`, `S09.config-var-expansion`, `S09.config-secrets`).** `Agent(mcp_config={"verify": MCPServer(command=python, args=[mcp_server.py, "stdio"], env={"TOKEN": "${MY_TOKEN}", "OTHER": "${UNSET_VAR:-dflt-other}"})})`, conversation secrets `API_KEY` and `MY_TOKEN`, and `HOST_ONLY` set only in the program's environment. The scripted agent calls each tool, passes `summary`/`security_risk` on one call, references secrets as `$API_KEY`, `${API_KEY}` and `${MISSING:-dflt}`, and registers, calls and removes a tool at runtime.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S09/conversation --arg main \
    --check error missing --check status eq finished \
    --check first_tools contains echo --check first_tools contains nested --check first_tools not-contains verify_echo \
    --check nested_schema.required eq '["config"]' --check nested_schema.config_type eq object \
    --check nested_schema.config_props eq '["limits", "tags", "title"]' --check nested_schema.tags_type eq array \
    --check nested_schema.max_type eq integer --check nested_schema.has_summary eq true \
    --check nested_schema.description eq "Accept a nested object." \
    --check kind_schema_props contains kind \
    --check texts.0 contains "echo: hello" --check texts.1 contains "echo: with extras" \
    --check texts.2 contains count=1 --check texts.3 contains count=2 \
    --check texts.4 contains "title=t tags=a,b max=3 note=None" --check texts.5 contains kind=special \
    --check texts.6 contains "tool failed: on purpose" --check results.7.content eq '["ImageContent", "TextContent"]' \
    --check texts.8 contains '{"answer":42' \
    --check texts.9 contains 321-terkes --check texts.10 contains 321-terkes \
    --check texts.11 contains 'echo: dflt $HOST_ONLY' --check texts.12 contains "echo: <secret-hidden>" \
    --check texts.13 contains 654-kot --check texts.14 contains dflt-other \
    --check texts.17 contains 42 --check errors eq '["fail"]' \
    --check calls_with_extra len-ge 1 --check calls_with_extra.0 gt 0 --check last_tools_have_extra eq false \
    --check secret_values_persisted len-eq 0
  ```
  The two `counter` calls share one server process (session reuse); `fail` comes back as an error observation; the secret reaches the server (reversed back as `321-terkes`) while an echoed value is masked; `$HOST_ONLY` stays literal because only secrets expand; the env placeholders resolved from the conversation secret (`654-kot` is `tok-456` reversed) and the default; `extra` is offered to the model after `register_extra` and gone after `remove_extra`; no secret value is in the persisted state or events.
- **Filtering tools (`S09.filter-tools-regex`).**
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S09/conversation --arg filter --check error missing --check tools eq '["counter", "echo", "finish", "think"]'
  ```
- **A server that fails to start (`S09.startup-failure-tolerated`).** A working server next to one that exits at startup; then the failing server alone.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S09/conversation --arg startup \
    --check error missing --check status eq finished --check tools contains good_echo \
    --check texts.0 contains "echo: still here" \
    --check only_broken_error missing --check only_broken_status eq finished \
    --check only_broken_tools eq '["finish", "think"]'
  ```
- **Disabled servers (`S09.enabled-flag`).** A conversation whose only server is disabled (its command would write a marker file), and `create_mcp_tools` with everything disabled or a disabled server beside an enabled one.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S09/conversation --arg disabled \
    --check error missing --check status eq finished --check tools eq '["finish", "think"]' --check marker_written eq false
  control-sdk program S09/client --arg timeout \
    --check all_disabled_error contains "All configured MCP servers are disabled: off" \
    --check mixed_enabled_names_sample len-eq 3
  ```
- **Listing timeout and unreachable servers (`S09.listing-timeout`).** A stdio server that sleeps 20 s before serving, listed with `timeout=2`, and a URL nothing listens on.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S09/client --arg timeout \
    --check slow_error contains "MCP tool listing timed out after 2 seconds" \
    --check slow_error contains "MCP servers configured: sleepy" --check slow_seconds lt 15 \
    --check unreachable_error eq "MCPError: MCP Connection Failure"
  ```
- **stdio, streamable HTTP and SSE together (`S09.stdio-server`, `S09.http-sse-server`).** One config with a stdio server, an HTTP server (`transport="http"`) and an SSE server given only a `/sse` URL.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S09/client --arg transports \
    --check has_prefixed eq '["local_echo", "web_echo", "stream_echo"]' --check unprefixed_echo eq false \
    --check echo.local contains "echo: local" --check echo.web contains "echo: web" \
    --check echo.stream contains "echo: stream" --check single_server_names_unprefixed eq true
  ```
- **Static auth headers (`S09.static-auth`).** One HTTP server, five configs with `none`, `bearer`, `api_key` (`header_name="X-Api-Key"`), `basic` and `header` credentials; the server's `headers` tool reports what arrived.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S09/client --arg auth \
    --check none len-eq 0 --check bearer.authorization eq "Bearer bear-tok" \
    --check apikey.x-api-key eq key-1 --check basic.authorization eq "Basic YW5uOnB3" \
    --check custom.x-team eq blue
  ```
- **OAuth tokens and refresh (`S09.oauth`).** `MCPOAuthAuthCredential(strategy="oauth2", authentication=MCPOAuthAuthentication(type="oauth", client_id=...))` with a token store holding what a finished login leaves: a valid token; an expired token with the server advertising `/oauth/token` in `/.well-known/oauth-authorization-server`; an expired token with no metadata.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S09/client --arg oauth \
    --check valid.authorization eq "Bearer stored-access-token" --check valid.server_log len-eq 0 \
    --check expired.authorization eq "Bearer fresh-access-token" \
    --check expired.server_log eq '["metadata", "/oauth/token grant=refresh_token refresh=stored-refresh-token"]' \
    --check expired_no_metadata.authorization eq "Bearer fresh-access-token" \
    --check expired_no_metadata.server_log eq '["/token grant=refresh_token refresh=stored-refresh-token"]'
  ```
- **Interactive OAuth login (`S09.oauth`), blocked.** The first login (dynamic client registration and the browser consent screen, as in `examples/01_standalone_sdk/08_mcp_with_oauth.py` against Notion) needs a browser and a Notion workspace.
- **A transient server error, and a closed client (`S09.session-recovery`).** The HTTP server answers the first `tools/call` with 503.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S09/client --arg recovery \
    --check transient_first.1 eq true --check transient_first.0 contains "503 Service Unavailable" \
    --check transient_second.1 eq false --check transient_second.0 contains count=1 \
    --check transient_third.0 contains count=2 \
    --check closed_client.1 eq true --check closed_client.0 contains "has been closed and cannot be reconnected"
  ```
- **A lost session: server restart or exit (`S09.session-recovery`).** The HTTP server is killed and restarted on the same port; the stdio server exits after answering `die`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S09/client --arg restart \
    --check http_before.0 contains count=1 \
    --check http_after_restart_again.1 eq false --check http_after_restart_again.0 contains count=1 \
    --check stdio_after_exit_again.1 eq false --check stdio_after_exit_again.0 contains count=1
  ```
  Expected: at most one failed call, then the client reconnects (a new session or process, so the counter starts again). Known failure (reproduced 2026-10-13, not yet filed): neither recovers. After the restart every call returns `Error calling MCP tool counter: Session terminated`; after the stdio server exits, every call returns `Error calling MCP tool counter: ` (empty), so the server's tools stay broken for the rest of the conversation. `MCPToolExecutor.call_tool` reconnects only when `client.is_connected()` is false, which stays true in both cases.
- **Config model, secrets, schemas, arguments and serialization without an agent (`S09.config-model`, `S09.config-secrets`, `S09.schema-conversion`, `S09.argument-validation`, `S09.observation`, `S09.tool-serialization`).**
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S09/client --arg config \
    --check unknown_field_error eq true --check stdio_without_command_error eq true \
    --check http_without_url_error eq true --check auth_and_header_error eq true \
    --check coerce_external eq '["x"]' --check create_rejects_external contains "create_mcp_tools expects native MCP servers" \
    --check secret_in_plain_dump eq false --check secret_in_cipher_dump eq false --check cipher_roundtrip eq env-secret \
    --check openai_props eq '["config", "note", "summary"]' --check openai_required eq '["config"]' \
    --check security_risk_added eq true --check responses_name eq nested --check responses_nested_type eq object \
    --check mcp_arguments eq '{"config": {"title": "t", "tags": ["x"], "limits": {"max": 1}}}' \
    --check extras_direct_error eq true --check kind_arguments eq '{"kind": "keep-me"}' \
    --check kind_call contains kind=keep-me --check invalid_error eq true --check missing_error eq true \
    --check fail.is_error eq true --check picture_content contains ImageContent \
    --check structured_text contains '"answer":42' \
    --check wire_spelling eq true --check restored_type eq MCPToolDefinition --check restored_same_schema eq true \
    --check legacy_key_rewritten eq true --check legacy_spelling_loads eq true --check frozen eq true \
    --check kind_in_dump eq MCPToolDefinition
  ```
- **A real model calls MCP tools (`S09.stdio-server`, `S09.schema-conversion`, `S09.secret-refs-in-calls`, `S09.observation`).** Live, against the local stdio server.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk program S09/live_mcp --check status eq finished --check add_result_42 eq true \
    --check reverse_expanded eq true --check tools_called contains add
  ```
- **Example 07 (`S09.stdio-server`, `S09.filter-tools-regex`, `S09.observation`).** Live; `uvx mcp-server-fetch` and `npx repomix` need network on the first run.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output "EXAMPLE_COST" -- python examples/01_standalone_sdk/07_mcp_integration.py
  ```

## Gotchas

- `examples/01_standalone_sdk/08_mcp_with_oauth.py` is in `_EXCLUDED_EXAMPLES` of `tests/examples/test_examples.py`; the OAuth flow has only the mocked tests in `tests/sdk/mcp/test_oauth.py`.
- Example 07 pins `mcp-server-fetch==2026.7.10` through `uvx` and `repomix@1.4.2` through `npx`; the first run downloads both, which can exceed the 30 s listing timeout on a cold cache. The conversation then logs `MCP server startup failed ... continuing without its tools` and the agent has no `fetch` tool instead of failing.
- MCP startup failures inside a conversation are warnings, not errors; check the log or `conversation.agent.tools_map` to confirm the MCP tools exist before judging the model.
- With two or more servers, tool names are prefixed `<server>_<tool>`; with one server they are not. A `filter_tools_regex` written for one case drops tools in the other.
- Remote `http://` URLs are accepted for `Agent.mcp_config`; only Agent Plugins packages (S08) require `https` for non-loopback hosts.
- A bare `MCPOAuthAuthCredential(strategy="oauth2")` without a token store leaves the flow to FastMCP's interactive browser login; in a sandbox without a browser, pass `mcp_oauth_token_storage` or a pre-registered client through `MCPOAuthAuthentication`.
- A server that cannot start (or exits during startup) does not cost the other servers their tools: with one good and one failing stdio server, the conversation still offers the good server's tools, prefixed.
- The model sees `summary` (and `security_risk`) on every MCP tool, but `tool.action_from_arguments({...})` called directly rejects those keys; strip them first when driving tools without an agent.
- Every MCP observation text starts with `[Tool '<name>' executed.]`; parse the tool's own output after it.
- MCP clients run on a background event loop thread; `on_tools_changed` and `on_tools_reconciled` callbacks run on that thread and must be thread-safe.
