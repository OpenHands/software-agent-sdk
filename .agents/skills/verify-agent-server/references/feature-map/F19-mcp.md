# MCP server settings, connection test and OAuth

How a consumer adds an MCP server for its agents: it stores one server at a
time in the persisted settings (`agent_settings.mcp_config`, keyed by a name of
the client's choice) without resending the catalog, can validate a candidate
first with a connection test that spawns or connects to it, lists its tools and
optionally calls one, and, for servers protected by OAuth, runs a
browser-coordinated consent job whose resulting token state is saved back into
the server's `auth.state`. Credentials (`env`, `headers`, `auth` values and
OAuth tokens) are redacted in responses, encrypted on disk and decrypted again
before a connection. Conversations started from these settings connect to every
enabled server and expose its tools to the agent; disabled servers stay stored
but are never spawned. When a conversation refreshes an expired OAuth token,
the rotated tokens are written back into the stored server, or, for a server
passed inline on the agent, posted to the app server's webhook. A conversation does not wait
for slow servers: it starts after a 1-second budget, and servers still
starting join the agent when ready, before its first model call. A running
conversation can also be given more servers through its own MCP route.

Source: `openhands-agent-server/openhands/agent_server/mcp_router.py`, `openhands-agent-server/openhands/agent_server/mcp_oauth_store.py`, `openhands-agent-server/openhands/agent_server/settings_router.py`, `openhands-agent-server/openhands/agent_server/_secrets_exposure.py`, `openhands-sdk/openhands/sdk/mcp/`, `openhands-sdk/openhands/sdk/settings/api_models.py`, `openhands-sdk/openhands/sdk/utils/redact.py`, `openhands-sdk/openhands/sdk/workspace/remote/base.py`, `clients/typescript/src/client/mcp-client.ts`, `clients/typescript/src/client/settings-client.ts`

Needs: `llm`

Launch: `--webhook-sink`

Routes: `POST /api/mcp/test`, `POST /api/mcp/oauth/start`,
`GET /api/mcp/oauth/status/{job_id}`, `POST /api/mcp/oauth/callback/{job_id}`,
`GET /api/mcp/oauth/redirect`,
`POST /api/settings/mcp/{settings_key}`, `PATCH /api/settings/mcp/{settings_key}`,
`DELETE /api/settings/mcp/{settings_key}`,
`POST /api/conversations/{conversation_id}/mcp`

## Sub-features

- `F19.auth`: every F19 route answers 401 without or with a wrong session key and stores or spawns nothing.
- `F19.settings-create`: `POST /api/settings/mcp/{settings_key}` stores one server (201, the whole redacted `SettingsResponse`), `GET /api/settings` shows it redacted, `X-Expose-Secrets: plaintext` returns the credentials, the file on disk holds them encrypted, and creating an existing key is 409 without changing it.
- `F19.settings-validation`: creating a stdio server without `command`, a remote server without `url`, an unknown field or transport, or `auth` next to an `Authorization` header is 422 and stores nothing. Same promise: `S09.config-model`.
- `F19.settings-empty-server`: a server body with neither `command` nor `url` (`{}`) is rejected with 422 instead of being stored.
- `F19.settings-patch`: `PATCH` merges sparsely (`enabled: false` keeps url and headers, a `null` header entry deletes only that entry, `enabled: null` restores `true`); an unknown key is 404, an unknown field or a merge that leaves an http server without `url` is 422.
- `F19.settings-delete`: `DELETE` removes one server and leaves the others and their credentials intact; deleting a missing key is 404.
- `F19.test-stdio`: `POST /api/mcp/test` spawns a stdio server, lists its tools, runs a requested `tool_call` and returns its text, and no stdio child survives the response. Same promise: `S09.stdio-server`.
- `F19.test-tool-errors`: a tool that raises, an unadvertised tool name and a tool that outlives the timeout are reported in `tool_result` with `is_error: true` while `ok` stays `true`. Same promise: `S09.observation`.
- `F19.test-connect-failures`: a missing executable, a closed port and a command that exits at once answer 200 with `ok: false`, an `error` and an `error_kind`. Same promise: `S09.listing-timeout`.
- `F19.test-timeout`: a stdio server that never completes the handshake yields `ok: false`, `error_kind: timeout` close to the requested `timeout`, and its process is killed. Same promise: `S09.listing-timeout`.
- `F19.test-validation`: a missing server, an empty command, an unknown transport, a `timeout` outside (0, 120], top-level `authentication` and `auth` next to an `Authorization` header are 422. Same promise: `S09.config-model`.
- `F19.test-encrypted-env`: `env` values encrypted by the settings API are decrypted before the stdio server starts, and the child does not inherit the agent server's own keys. Same promise: `S09.config-secrets`.
- `F19.test-remote-bearer`: a streamable-HTTP server behind a bearer token connects with `auth.strategy: bearer`, an `Authorization` header (also as `type: shttp`) or `auth.strategy: header`, and a wrong token answers `ok: false` naming the 401 (known bug #6687: the status is lost). Same promise: `S09.http-sse-server`, `S09.static-auth`.
- `F19.oauth-start-errors`: OAuth start is 400 for a stdio or non-oauth2 server, reports `ok: false` with a failed job for an unreachable server (whose callback is then 409), and finishes without consent for a server that needs no OAuth.
- `F19.oauth-unknown-job`: status and callback for an unknown job id are 404, and a callback without `callback_url` is 422.
- `F19.oauth-flow`: against a local OAuth-protected MCP server, start returns an `authorization_url`, status reports `authorizing` and `callback_ready`, and submitting the browser's redirect URL completes the job with tools, the tool result and an encrypted `oauth_state`. Same promise: `S09.oauth`.
- `F19.oauth-callback-replay`: submitting a callback again after the job finished answers 200 with the job's status, not 500, and the job stays `succeeded`.
- `F19.oauth-callback-bad-port`: a callback URL with a port outside 0-65535 is rejected with 400, not 500.
- `F19.oauth-callback-validation`: on a live job, an https URL, a foreign host, another port or another path is 400 and the job keeps waiting; the redirect URL with `127.0.0.1` instead of `localhost` completes it.
- `F19.oauth-consent-denied`: submitting the redirect a provider sends when the user denies consent (`error=access_denied`) answers `ok: false` and fails the job without tokens.
- `F19.oauth-consent-timeout`: a consent that is not completed within the job's `timeout` fails the job with `error_kind: timeout`.
- `F19.oauth-public-redirect`: on a server that knows its external URL (`OH_WEB_URL`), OAuth start registers `{web_url}/api/mcp/oauth/redirect` as the redirect URI, path prefix included, while a server without one keeps the loopback callback `http://localhost:<port>/callback`.
- `F19.oauth-redirect-route`: with `callback_base_url` set to the server's own base URL (and no `web_url`), the provider redirects the browser to `<base>/api/mcp/oauth/redirect`, which answers 200 "Authorization complete" without a session key; the job then succeeds with an encrypted `oauth_state`, and a second delivery of the same redirect is 409.
- `F19.oauth-redirect-unknown-state`: the redirect route answers 400 "Authorization failed" for a forged, empty or missing `state` and leaves the waiting job untouched, while the job's real `state` with a provider error is recognized (400 naming the error) and fails the job.
- `F19.oauth-callback-base-url-validation`: a `callback_base_url` with a query, a fragment, userinfo, no host or a non-http(s) scheme is 422; a plain https base with a path prefix is accepted.
- `F19.oauth-web-url-precedence`: on a server with `OH_WEB_URL`, a `callback_base_url` in the start body is ignored for the `web_url` redirect URI, and the redirect delivered to that server's route completes the job.
- `F19.oauth-callback-no-redirect`: the callback route forwards the submitted query only to the job's own callback address and does not follow a redirect it gets from there (known bug #6808: the redirect is followed).
- `F19.oauth-inline-writeback`: an OAuth server passed inline on a conversation's agent (not in settings) whose access token has expired is refreshed when the conversation connects, keeps its tools, and the rotated tokens are posted with the run's session key to the webhook's `/mcp-oauth-state`, not stored in settings. Same promise: `S09.oauth`.
- `F19.oauth-stored-state`: the returned `oauth_state` used as `auth.state` connects without consent (test, and start without `authorization_url`), and stored through the settings route it is redacted, encrypted on disk and decrypted to the real token.
- `F19.disabled-server`: a conversation started from the settings exposes the tools of enabled servers (including the OAuth server from its stored tokens) and neither exposes nor spawns a disabled server. Same promise: `S09.enabled-flag`.
- `F19.conversation-tool`: a model-driven conversation calls the configured stdio server's `qa_echo` tool, the `ObservationEvent` (REST and WebSocket) carries `QA_MCP_ECHO:hi`, and deleting the conversation stops the stdio child; an embedded resource's text and a structured-only result reach the observation (known bug #6993: both are dropped), and a single server that advertises `terminal` next to the built-in terminal gets it as `<server>_terminal` while both run (known bug #6992: a server that joins after the startup budget loses all its tools, so its other tools are `not found`; one ready within the budget makes the first message 500 `Duplicate tool names found: {'terminal'}`). Same promise: `S09.observation`.
- `F19.oauth-refresh-writeback`: when a stored OAuth server's access token has expired, the next conversation refreshes it, keeps the server's tools, and writes the rotated token and new expiry back into the settings (encrypted on disk) without posting them to the webhook. Same promise: `S09.oauth`.
- `F19.conversation-secrets-at-rest`: a conversation started from the settings persists its agent's MCP credentials (a disabled server's `env`, OAuth tokens) encrypted, and no MCP credential this family used appears in plaintext under the conversations directory or in the server log. Same promise: `S09.config-secrets`, `S19.mcp-config-secrets`.
- `F19.restart`: stored MCP servers and their credentials survive a restart (plaintext GET and the SDK's `RemoteWorkspace.get_mcp_config()`), while in-memory OAuth jobs are gone (404).
- `F19.conversation-mcp-add`: `POST /api/conversations/{conversation_id}/mcp` on a conversation whose agent is initialized starts the server before it answers 200 `{"success": true}`, `GET /api/conversations/{conversation_id}` and `base_state.json` show it in `agent.mcp_config`, the model's next request offers its tools as `<server>_<tool>`, a call returns the tool's result, deleting the conversation stops the child, and `/server_info` advertises `conversation_mcp_install_v1` and `conversation_configuration_additions_v1`. Same promise: `S09.runtime-add-servers`.
- `F19.conversation-mcp-add-before-init`: on a conversation without a first message the route only records the server (no process, fast answer), with `env` values encrypted by the settings API decrypted for the child and stored encrypted in `base_state.json`; the first message connects it like any configured server. Same promise: `S09.runtime-add-servers`.
- `F19.conversation-mcp-add-errors`: an unknown conversation is 404, a server name the agent already has and an SSE server are 400 with the reason, a server without `command`/`url`, an unknown field and a non-UUID id are 422, none of them changes the agent's `mcp_config`, and a disabled server is stored without being spawned.
- `F19.conversation-mcp-add-start-failure`: a server that cannot start (a command that exits at once) is refused with the route's 400 naming the server and the cause (`Could not start MCP server <name>: Connection closed`, since [#6928](https://github.com/OpenHands/software-agent-sdk/pull/6928)) and is not added.
- `F19.conversation-mcp-add-restart`: a server added at runtime is still in the conversation's agent after a restart, and the tools the agent already called keep working under the same names (known bug [#6927](https://github.com/OpenHands/software-agent-sdk/issues/6927), closed 2026-11-18 with no fix merged; fix proposed in [#6929](https://github.com/OpenHands/software-agent-sdk/pull/6929): with one server the tools come back unprefixed, so `qa-f19-rs_qa_echo` is `Tool ... not found`). Same promise: `S09.runtime-add-resume`.
- `F19.conversation-mcp-slow-start`: a conversation whose stdio server delays its handshake by 8 s is created within a few seconds, the server is spawned, and the agent's first model request waits for it and offers its tools; the agent's call returns its result. Same promise: `S09.startup-budget`.
- `F19.conversation-mcp-late-failure`: a server that fails after the startup budget costs only its own tools: the conversation starts at once, the first step waits for the failure, the other server's tools are offered, the run finishes and the log names the failed server.

## How to get to it (agent POV)

- REST, settings: `POST|PATCH|DELETE /api/settings/mcp/{settings_key}` (one
  server per call, each returns the whole redacted `SettingsResponse`), read
  back with `GET /api/settings` (`X-Expose-Secrets: plaintext|encrypted` for
  credentials). `PATCH /api/settings` with `agent_settings_diff.mcp_config` is
  the bulk alternative (settings family). All recipes here use the per-server
  routes.
- REST, validation before saving: `POST /api/mcp/test` (accepts the native
  shape `{"command"|"url", "transport"}` or the legacy `type`:
  `stdio|http|shttp|streamable-http|sse`); never stores anything.
- REST, OAuth install: `POST /api/mcp/oauth/start` (same body as the test, with
  `server.auth.strategy: oauth2`), `GET /api/mcp/oauth/status/{job_id}`,
  `POST /api/mcp/oauth/callback/{job_id}` with the redirect URL the browser
  landed on. The client then saves `oauth_state` under the server's
  `auth.state` through the settings route.
- REST, browser redirect (no session key, not in the OpenAPI document):
  `GET /api/mcp/oauth/redirect?code=...&state=...`. When the server has
  `web_url` (`OH_WEB_URL`), start registers `{web_url}/api/mcp/oauth/redirect`
  as the provider's redirect URI (also for a pre-registered client); without
  it, `callback_base_url` in the start body (the server's base URL as the
  browser reaches it) does the same for a dynamically registered client. The
  route finds the pending job by the OAuth `state`, answers an HTML page, and
  the job exchanges the code; the client polls the status route. Without
  either, the redirect URI is the probe's loopback listener and the client
  submits the redirect URL to the callback route.
- Conversations: `POST /api/conversations` with `agent_settings` from
  `GET /api/settings` (what `conversation start` and Agent Canvas do) carries
  `mcp_config`; the server connects to the enabled servers when the agent
  initializes (on the first message, queued or run) and lists the MCP tools in
  the first `SystemPromptEvent`.
- Token refresh during a conversation (no route): the server's MCP tool
  provider serves FastMCP's OAuth client from the stored `auth.state` and
  writes refreshed tokens back into the settings; for an OAuth server passed
  inline on the agent it keeps them in memory and POSTs
  `{"server_url", "oauth_state"}` with `X-Session-API-Key` to every configured
  webhook's `<base_url>/mcp-oauth-state`, so the app server can persist them
  (driven with `launch --webhook-sink` and `sink read`).
- REST, one conversation: `POST /api/conversations/{conversation_id}/mcp`
  with `{"mcp_servers": {"<name>": <MCPServer>}}` adds servers to that
  conversation's agent only (settings untouched). When the agent is already
  initialized the route connects them before answering (their tools are
  named `<name>_<tool>`); before the first message it only records them.
  Encrypted credential values are decrypted with the server's key first.
  Clients detect it through `GET /server_info` `capabilities`
  (`conversation_mcp_install_v1`, `conversation_configuration_additions_v1`).
  The server side is the SDK's `LocalConversation.add_mcp_servers()` (S09);
  `RemoteConversation` and the TypeScript client have no method for it.
- SDK: `RemoteWorkspace.get_mcp_config()` reads the stored servers with
  plaintext credentials (exercised in `F19.restart` through `exec`).
- TypeScript client: `MCPClient.testServer`, `startOAuth`, `getOAuthStatus`,
  `submitOAuthCallback` (version-gated as `mcp-test` 1.23.0 and `mcp-oauth`
  1.31.0) and `SettingsClient.createMcpServer`, `patchMcpServer`,
  `deleteMcpServer`. Not driven here; the REST recipes cover the same calls.
- Agent Canvas (context only): the "Add MCP server" dialog tests, runs the
  OAuth popup and saves the server through these routes.

## Driving it with control-agent-server

Preconditions:

- A run launched with a recording webhook sink (`launch --new --webhook-sink`,
  the family's `Launch:` flags; only `F19.oauth-inline-writeback` and
  `F19.oauth-refresh-writeback` read it), `doctor` ok, `$DEEPSEEK_API_KEY` set
  (only `F19.conversation-tool` uses the model), and `jq`, `curl` and `pgrep`
  on `PATH`.
- The CLI's stdio fixture (`fixture mcp-server`, tool `qa_echo`) plus four
  recipe-local fixtures written below (no CLI verb exists for them): a stdio
  server with `qa_env`, `qa_sleep` and `qa_fail` whose first argument delays
  the MCP handshake, a stdio server whose results are not plain text
  (`qa_resource`, one embedded resource; `qa_structured_only`, only
  structured content) and that also advertises `terminal` and `qa_sibling`,
  an HTTP FastMCP server with `read_subject` and `qa_wait`
  in three modes (`none`, `bearer`
  with the static token `qa-f19-bearer`, and `oauth` with FastMCP's
  `InMemoryOAuthProvider`, which approves every authorization request), and an
  SDK program. The three HTTP servers run as `helper`s on free ports, so they
  outlive the Preconditions bullet that starts them; each exits after 25
  minutes or 60 seconds after the run's `/alive` stops answering, the last
  bullet stops them, and `stop` ends any that remain.
  ```sh
  control-agent-server llm preset deepseek
  MCP=$(control-agent-server fixture mcp-server --name qa-f19 | jq -c .mcp_server)
  MCP_CMDLINE=$(echo "$MCP" | jq -r '[.command] + .args | join(" ")')
  PY=$(echo "$MCP" | jq -r .command)
  test -x "$PY"
  F19=$AGENT_SERVER_VERIFY_RUN/fixtures/f19
  mkdir -p "$F19"
  cat > "$F19/qa_f19_tools.py" <<'EOF'
  """F19 stdio MCP fixture; argv[1] delays the MCP handshake by that many seconds."""
  import os
  import sys
  import time

  from fastmcp import FastMCP

  if len(sys.argv) > 1:
      time.sleep(float(sys.argv[1]))

  mcp = FastMCP("qa-f19-tools")


  @mcp.tool()
  def qa_env(name: str) -> str:
      """Return one environment variable of this MCP server process."""
      return "QA_ENV:" + os.environ.get(name, "<unset>")


  @mcp.tool()
  def qa_sleep(seconds: float) -> str:
      """Sleep, then answer."""
      time.sleep(seconds)
      return "QA_SLEPT"


  @mcp.tool()
  def qa_fail() -> str:
      """Always fail."""
      raise ValueError("QA_MCP_FAIL")


  if __name__ == "__main__":
      mcp.run()
  EOF
  cat > "$F19/qa_f19_payload.py" <<'EOF'
  """F19 stdio MCP fixture: non-text results, and a tool named like the built-in terminal; argv[1] delays the MCP handshake."""
  import sys
  import time

  from fastmcp import FastMCP
  from fastmcp.tools import ToolResult
  from mcp import types

  if len(sys.argv) > 1:
      time.sleep(float(sys.argv[1]))

  mcp = FastMCP("qa-f19-payload")


  @mcp.tool(output_schema=None)
  def qa_resource() -> ToolResult:
      """Return one embedded text resource."""
      text = types.TextResourceContents(uri="file:///qa-f19/payload.json", mimeType="application/json", text='{"payload": "QA_F19_RESOURCE_TEXT"}')
      return ToolResult(content=[types.EmbeddedResource(type="resource", resource=text)])


  @mcp.tool(output_schema=None)
  def qa_structured_only() -> ToolResult:
      """Return structured content without content blocks."""
      return ToolResult(content=[], structured_content={"payload": "QA_F19_STRUCTURED"})


  @mcp.tool(name="terminal")
  def qa_shadow_terminal(command: str) -> str:
      """An MCP tool with the built-in terminal tool's name."""
      return "QA_F19_MCP_TERMINAL:" + command


  @mcp.tool()
  def qa_sibling() -> str:
      """A tool next to the shadowing one."""
      return "QA_F19_SIBLING"


  if __name__ == "__main__":
      mcp.run()
  EOF
  cat > "$F19/qa_f19_http.py" <<'EOF'
  """F19 HTTP MCP fixture: argv = port, mode (none|bearer|oauth), ttl seconds, run /alive URL."""
  import asyncio
  import os
  import sys
  import threading
  import time
  import urllib.request

  from fastmcp import FastMCP

  port, mode, ttl, alive_url = int(sys.argv[1]), sys.argv[2], float(sys.argv[3]), sys.argv[4]
  auth = None
  if mode == "bearer":
      from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

      auth = StaticTokenVerifier(tokens={"qa-f19-bearer": {"client_id": "qa", "scopes": []}})
  elif mode == "oauth":
      from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
      from mcp.server.auth.settings import ClientRegistrationOptions

      auth = InMemoryOAuthProvider(
          base_url=f"http://127.0.0.1:{port}",
          client_registration_options=ClientRegistrationOptions(
              enabled=True, valid_scopes=["mail.read"], default_scopes=["mail.read"]
          ),
          required_scopes=["mail.read"],
      )
  mcp = FastMCP("qa-f19-http", auth=auth)


  @mcp.tool()
  def read_subject(subject: str) -> str:
      """Return a marked subject line."""
      return f"QA_HTTP_SUBJECT:{subject}"


  @mcp.tool()
  async def qa_wait(seconds: float) -> str:
      """Wait, then answer."""
      await asyncio.sleep(seconds)
      return "QA_HTTP_WAITED"


  def watchdog() -> None:
      opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
      started = last_ok = time.monotonic()
      while True:
          time.sleep(2)
          now = time.monotonic()
          try:
              opener.open(alive_url, timeout=2)
              last_ok = now
          except Exception:
              pass
          if now - started > ttl or now - last_ok > 60:
              os._exit(0)


  threading.Thread(target=watchdog, daemon=True).start()
  mcp.run(transport="http", host="127.0.0.1", port=port, path="/mcp", show_banner=False)
  EOF
  cat > "$F19/sdk_mcp_config.py" <<'EOF'
  import os

  from openhands.sdk.workspace import RemoteWorkspace

  ws = RemoteWorkspace(
      host=os.environ["AGENT_SERVER_URL"],
      api_key=os.environ["SESSION_API_KEY"],
      working_dir=os.environ["AGENT_SERVER_VERIFY_RUN"],
  )
  cfg = ws.get_mcp_config()
  print("SDK_MCP_KEYS=" + ",".join(sorted(cfg)))
  server = cfg["qa-env"]
  token = (server.env or {})["QA_F19_TOKEN"].get_secret_value()
  print("SDK_MCP_ENV_OK=" + str(token == "qa-f19-env-secret" and server.enabled is False))
  EOF
  TOOLS_SRV=$(jq -nc --arg py "$PY" --arg s "$F19/qa_f19_tools.py" '{command: $py, args: [$s]}')
  PAYLOAD_SRV=$(jq -nc --arg py "$PY" --arg s "$F19/qa_f19_payload.py" '{command: $py, args: [$s]}')
  RUN_URL=$(control-agent-server config | jq -r .url)
  free_port() { "$PY" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }
  BEARER_PORT=$(free_port)
  OAUTH_PORT=$(free_port)
  NONE_PORT=$(free_port)
  DEAD_PORT=$(free_port)
  control-agent-server helper start --name qa-f19-http-bearer --log "$F19/http-bearer.log" \
    -- "$PY" "$F19/qa_f19_http.py" "$BEARER_PORT" bearer 1500 "$RUN_URL/alive" >/dev/null
  control-agent-server helper start --name qa-f19-http-oauth --log "$F19/http-oauth.log" \
    -- "$PY" "$F19/qa_f19_http.py" "$OAUTH_PORT" oauth 1500 "$RUN_URL/alive" >/dev/null
  control-agent-server helper start --name qa-f19-http-none --log "$F19/http-none.log" \
    -- "$PY" "$F19/qa_f19_http.py" "$NONE_PORT" none 1500 "$RUN_URL/alive" >/dev/null
  for port in "$BEARER_PORT" "$OAUTH_PORT" "$NONE_PORT"; do
    for i in $(seq 1 80); do curl -s --noproxy '*' -o /dev/null "http://127.0.0.1:$port/mcp" && break; sleep 0.25; done
  done
  test "$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' "http://127.0.0.1:$BEARER_PORT/mcp")" = 401
  test "$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' "http://127.0.0.1:$OAUTH_PORT/mcp")" = 401
  test "$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' "http://127.0.0.1:$NONE_PORT/mcp")" != 000
  OAUTH_SRV=$(jq -nc --arg url "http://127.0.0.1:$OAUTH_PORT/mcp" '{type: "http", url: $url, auth: {strategy: "oauth2", authentication: {type: "oauth", client_auth_method: "none", scopes: ["mail.read"]}}}')
  QS_OF='import json, sys, urllib.parse as u; print(json.dumps({k: v[0] for k, v in u.parse_qs(u.urlsplit(sys.argv[1]).query).items()}))'
  "$PY" -c "$QS_OF" 'http://x.invalid/a?redirect_uri=http%3A%2F%2Fy&state=s' | control-agent-server assert --check redirect_uri eq http://y --check state eq s
  ```

- **Auth (`F19.auth`).** Every route without a key, or with a wrong one.
  ```sh
  control-agent-server api POST /api/mcp/test --auth none --json '{"server":{"command":"/bin/true"}}' --expect 401
  control-agent-server api POST /api/mcp/oauth/start --auth bad --json '{"server":{"command":"/bin/true"}}' --expect 401
  control-agent-server api GET /api/mcp/oauth/status/qa-f19-nope --auth none --expect 401
  control-agent-server api POST /api/mcp/oauth/callback/qa-f19-nope --auth none --json '{"callback_url":"http://localhost:1/callback"}' --expect 401
  control-agent-server api POST /api/settings/mcp/qa-f19-auth --auth none --json '{"command":"/bin/true"}' --expect 401
  control-agent-server api PATCH /api/settings/mcp/qa-f19-auth --auth bad --json '{"enabled":false}' --expect 401
  control-agent-server api DELETE /api/settings/mcp/qa-f19-auth --auth none --expect 401
  control-agent-server api GET /api/settings --check agent_settings.mcp_config.qa-f19-auth missing --save F19.auth/settings-after
  ```
  All seven answer 401 and nothing named `qa-f19-auth` was stored.
- **Create one server (`F19.settings-create`).** A remote server with two
  header credentials, then the same key again.
  ```sh
  control-agent-server api POST /api/settings/mcp/qa-remote --expect 201 \
    --json '{"transport":"http","url":"https://example.invalid/mcp","headers":{"Authorization":"Bearer qa-f19-t0k","X-QA":"qa-f19-x"},"description":"QA remote"}' \
    --check agent_settings.mcp_config.qa-remote.headers.Authorization eq '**********' \
    --check agent_settings.mcp_config.qa-remote.enabled eq true \
    --check agent_settings.schema_version exists --save F19.settings-create/create
  control-agent-server api GET /api/settings --check agent_settings.mcp_config.qa-remote.url eq https://example.invalid/mcp \
    --check agent_settings.mcp_config.qa-remote.headers.X-QA eq '**********' --save F19.settings-create/get-redacted
  control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' \
    --check agent_settings.mcp_config.qa-remote.headers.Authorization eq 'Bearer qa-f19-t0k' \
    --check agent_settings.mcp_config.qa-remote.headers.X-QA eq qa-f19-x
  control-agent-server state cat home/.openhands/settings.json --not-contains qa-f19-t0k \
    --check agent_settings.mcp_config.qa-remote.headers.Authorization matches '^gAAAA'
  control-agent-server api POST /api/settings/mcp/qa-remote --json '{"transport":"http","url":"https://other.invalid/mcp"}' \
    --expect 409 --check detail eq "MCP server 'qa-remote' already exists" --save F19.settings-create/conflict
  control-agent-server api GET /api/settings --check agent_settings.mcp_config.qa-remote.url eq https://example.invalid/mcp
  ```
  The create answers 201 with the whole settings document and both header
  values as `**********`; plaintext GET returns them, `settings.json` holds
  Fernet ciphertext only, and the second create is 409 with the url unchanged.
- **Invalid servers (`F19.settings-validation`).**
  ```sh
  control-agent-server api POST /api/settings/mcp/qa-bad --json '{"transport":"stdio"}' --expect 422 --check detail.0.msg contains "require 'command'"
  control-agent-server api POST /api/settings/mcp/qa-bad --json '{"transport":"http"}' --expect 422 --check detail.0.msg contains "require 'url'"
  control-agent-server api POST /api/settings/mcp/qa-bad --json '{"url":"https://x.invalid/mcp","bogus":1}' --expect 422 --check detail.0.type eq extra_forbidden
  control-agent-server api POST /api/settings/mcp/qa-bad --json '{"type":"http","url":"https://x.invalid/mcp"}' --expect 422 --check detail.0.type eq extra_forbidden
  control-agent-server api POST /api/settings/mcp/qa-bad --json '{"transport":"websocket","url":"ws://x.invalid"}' --expect 422 --check detail.0.type eq literal_error
  control-agent-server api POST /api/settings/mcp/qa-bad --expect 422 --save F19.settings-validation/auth-and-header \
    --json '{"transport":"http","url":"https://x.invalid/mcp","headers":{"Authorization":"a"},"auth":{"strategy":"bearer","value":"b"}}' \
    --check detail.0.msg contains 'cannot be combined'
  control-agent-server api GET /api/settings --check agent_settings.mcp_config.qa-bad missing
  ```
  Every body is 422 (the settings routes take `transport`, not the test
  route's legacy `type`) and `qa-bad` never appears.
- **Server with neither command nor url (`F19.settings-empty-server`).**
  `MCPServer` requires `command` or `url` whatever the `transport`, so `{}`
  is rejected with 422 and nothing is stored (a stored server like that once
  cost every new conversation all of its MCP tools). If the POST is not
  rejected, the same line removes the stored entry before failing, so later
  bullets are unaffected.
  ```sh
  control-agent-server api GET /api/settings --check agent_settings.mcp_config.qa-empty missing
  control-agent-server api POST /api/settings/mcp/qa-empty --json '{}' --expect 422 --check detail.0.msg contains "require 'command' or 'url'" --save F19.settings-empty-server/create || { control-agent-server api DELETE /api/settings/mcp/qa-empty --expect 200,404 >/dev/null; false; }
  control-agent-server api GET /api/settings --check agent_settings.mcp_config.qa-empty missing
  ```
  The POST answers 422 with `MCP servers require 'command' or 'url'` and
  `qa-empty` never appears.
- **Sparse patch (`F19.settings-patch`).** Toggle, edit one header, restore
  the default, then the error cases.
  ```sh
  control-agent-server api PATCH /api/settings/mcp/qa-remote --json '{"enabled":false}' --expect 200 \
    --check agent_settings.mcp_config.qa-remote.enabled eq false \
    --check agent_settings.mcp_config.qa-remote.url eq https://example.invalid/mcp --save F19.settings-patch/disable
  control-agent-server api PATCH /api/settings/mcp/qa-remote --json '{"headers":{"X-QA":null,"X-New":"qa-f19-new"}}' --expect 200
  control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' \
    --check agent_settings.mcp_config.qa-remote.headers.Authorization eq 'Bearer qa-f19-t0k' \
    --check agent_settings.mcp_config.qa-remote.headers.X-New eq qa-f19-new \
    --check agent_settings.mcp_config.qa-remote.headers.X-QA missing \
    --check agent_settings.mcp_config.qa-remote.description eq 'QA remote' \
    --check agent_settings.mcp_config.qa-remote.enabled eq false --save F19.settings-patch/get-plaintext
  control-agent-server api PATCH /api/settings/mcp/qa-remote --json '{"enabled":null}' --expect 200 \
    --check agent_settings.mcp_config.qa-remote.enabled eq true
  control-agent-server api PATCH /api/settings/mcp/qa-remote --json '{"url":null}' --expect 422 --check detail eq 'Settings validation failed'
  control-agent-server api PATCH /api/settings/mcp/qa-remote --json '{"bogus":1}' --expect 422 --check detail.0.type eq extra_forbidden
  control-agent-server api PATCH /api/settings/mcp/qa-f19-nope --json '{"enabled":false}' --expect 404 \
    --check detail eq "MCP server 'qa-f19-nope' was not found" --save F19.settings-patch/unknown
  control-agent-server api GET /api/settings --check agent_settings.mcp_config.qa-remote.url eq https://example.invalid/mcp \
    --check agent_settings.mcp_config.qa-remote.enabled eq true \
    --check agent_settings.mcp_config.qa-f19-nope missing --save F19.settings-patch/get-after-errors
  ```
  The first patch keeps url and headers; the header patch removes only `X-QA`
  and adds `X-New` while `Authorization` survives; `enabled: null` brings back
  `true` (also on the final read); the three bad patches change nothing and
  the 404 creates nothing.
- **Delete one server (`F19.settings-delete`).** A sibling with an `env`
  credential, then delete both.
  ```sh
  control-agent-server api POST /api/settings/mcp/qa-sibling --json '{"command":"/bin/true","env":{"QA_F19_SIBLING":"qa-f19-sib"}}' --expect 201
  control-agent-server api DELETE /api/settings/mcp/qa-remote --expect 200 \
    --check agent_settings.mcp_config.qa-remote missing --check agent_settings.mcp_config.qa-sibling exists --save F19.settings-delete/delete
  control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' \
    --check agent_settings.mcp_config.qa-remote missing \
    --check agent_settings.mcp_config.qa-sibling.env.QA_F19_SIBLING eq qa-f19-sib
  control-agent-server api DELETE /api/settings/mcp/qa-sibling --expect 200 --check agent_settings.mcp_config.qa-sibling missing
  control-agent-server api DELETE /api/settings/mcp/qa-sibling --expect 404 --check detail eq "MCP server 'qa-sibling' was not found"
  ```
  The sibling keeps its decrypted `env` value after the other server is
  deleted; the second delete of the same key is 404. `mcp_config` is empty
  again, which the conversation bullets rely on.
- **Connection test body validation (`F19.test-validation`).**
  ```sh
  control-agent-server api POST /api/mcp/test --json '{}' --expect 422 --check detail.0.loc contains server
  control-agent-server api POST /api/mcp/test --json '{"server":{"transport":"stdio","command":""}}' --expect 422 --check detail.0.type eq string_too_short
  control-agent-server api POST /api/mcp/test --json '{"server":{"transport":"websocket","url":"ws://127.0.0.1:9"}}' --expect 422 --check detail.0.type eq union_tag_invalid
  control-agent-server api POST /api/mcp/test --json '{"server":{"command":"/bin/true"},"timeout":0}' --expect 422 --check detail.0.type eq greater_than
  control-agent-server api POST /api/mcp/test --json '{"server":{"command":"/bin/true"},"timeout":121}' --expect 422 --check detail.0.type eq less_than_equal
  control-agent-server api POST /api/mcp/test --json '{"server":{"url":"http://127.0.0.1:9/mcp","authentication":{"type":"oauth"}}}' --expect 422 \
    --check detail.0.msg contains auth.authentication
  control-agent-server api POST /api/mcp/test --expect 422 --save F19.test-validation/auth-and-header \
    --json '{"server":{"type":"http","url":"http://127.0.0.1:9/mcp","headers":{"Authorization":"Bearer a"},"auth":{"strategy":"bearer","value":"b"}}}' \
    --check detail.0.msg contains 'cannot be combined'
  ```
  All seven are 422 before anything is spawned or contacted.
- **Test a stdio server (`F19.test-stdio`).** List, then list and call
  `qa_echo`, then look for a surviving child.
  ```sh
  control-agent-server api POST /api/mcp/test --json "$(jq -nc --argjson s "$MCP" '{name: "qa-f19", server: $s, timeout: 30}')" \
    --expect 200 --check ok eq true --check tools contains qa_echo --check tool_result missing --save F19.test-stdio/list
  control-agent-server api POST /api/mcp/test --expect 200 --save F19.test-stdio/tool-call \
    --json "$(jq -nc --argjson s "$MCP" '{name: "qa-f19", server: ($s + {type: "stdio"}), timeout: 30, tool_call: {name: "qa_echo", arguments: {text: "hi"}}}')" \
    --check ok eq true --check tool_result.is_error eq false --check tool_result.text eq QA_MCP_ECHO:hi
  if pgrep -fx "$MCP_CMDLINE"; then echo 'a stdio MCP child survived the test' >&2; false; fi
  ```
  Both answer `ok: true` with `tools: ["qa_echo"]`; the second (explicit
  `type: stdio`) returns `QA_MCP_ECHO:hi`, and no process with the fixture's
  command line is left.
- **Tool failures stay in `tool_result` (`F19.test-tool-errors`).**
  ```sh
  control-agent-server api POST /api/mcp/test --json "$(jq -nc --argjson s "$TOOLS_SRV" '{server: $s, timeout: 30, tool_call: {name: "qa_fail"}}')" \
    --expect 200 --check ok eq true --check tool_result.is_error eq true --check tool_result.text contains QA_MCP_FAIL --save F19.test-tool-errors/raises
  control-agent-server api POST /api/mcp/test --json "$(jq -nc --argjson s "$TOOLS_SRV" '{server: $s, timeout: 30, tool_call: {name: "qa_nope"}}')" \
    --expect 200 --check ok eq true --check tool_result.is_error eq true --check tool_result.text contains 'not advertised by server'
  control-agent-server api POST /api/mcp/test --timeout 20 --expect-max-ms 15000 --save F19.test-tool-errors/slow-tool \
    --json "$(jq -nc --argjson s "$TOOLS_SRV" '{server: $s, timeout: 3, tool_call: {name: "qa_sleep", arguments: {seconds: 30}}}')" \
    --expect 200 --check ok eq true --check tool_result.is_error eq true --check tool_result.text contains 'timed out after 3.0 seconds'
  ```
  `ok` stays `true` in all three because the server connected and listed; the
  slow call is cut at the request's `timeout` (the whole request takes about 6
  seconds; `--expect-max-ms` fails it if the 30-second sleep ran out).
- **Connection failures (`F19.test-connect-failures`).** Always HTTP 200.
  ```sh
  control-agent-server api POST /api/mcp/test --json '{"name":"qa-bad","server":{"command":"/nonexistent/qa-f19-bin"},"timeout":10}' --expect 200 \
    --check ok eq false --check error_kind eq connection --check error contains 'No such file' --save F19.test-connect-failures/bad-command
  control-agent-server api POST /api/mcp/test --json "{\"server\":{\"type\":\"http\",\"url\":\"http://127.0.0.1:$DEAD_PORT/mcp\"},\"timeout\":5}" --expect 200 \
    --check ok eq false --check error_kind eq connection --save F19.test-connect-failures/closed-port
  control-agent-server api POST /api/mcp/test --json '{"server":{"command":"/bin/false"},"timeout":10}' --expect 200 \
    --check ok eq false --check error exists --check error_kind exists
  ```
  The missing executable and the closed port are `error_kind: connection`; a
  command that exits at once is `ok: false` (today `error_kind: unknown`,
  `McpError: Connection closed`).
- **Handshake timeout (`F19.test-timeout`).** The tools fixture sleeps 60
  seconds before speaking MCP; the request allows 3.
  ```sh
  SLOW_BODY=$(jq -nc --arg py "$PY" --arg s "$F19/qa_f19_tools.py" '{name: "qa-slow", server: {command: $py, args: [$s, "60"]}, timeout: 3}')
  T0=$SECONDS
  control-agent-server api POST /api/mcp/test --timeout 20 --expect 200 --expect-max-ms 12000 --save F19.test-timeout/slow-start --json "$SLOW_BODY" \
    --check ok eq false --check error_kind eq timeout --check error contains 'timed out after 3.0 seconds' &
  SLOW_REQ=$!
  for i in $(seq 1 40); do pgrep -fx "$PY $F19/qa_f19_tools.py 60" >/dev/null && break; sleep 0.1; done
  pgrep -fx "$PY $F19/qa_f19_tools.py 60"
  wait "$SLOW_REQ"
  test $((SECONDS - T0)) -ge 3
  if pgrep -fx "$PY $F19/qa_f19_tools.py 60"; then echo 'the slow stdio child survived the timeout' >&2; false; fi
  ```
  While the request is in flight the sleeping child exists (so the final
  `pgrep` can see it); the answer arrives after at least 3 and well under 12
  seconds (about 5) with `error_kind: timeout`, and the child is gone.
- **Encrypted env from settings (`F19.test-encrypted-env`).** Store a server
  with an `env` credential, take its ciphertext the way Agent Canvas does
  (`X-Expose-Secrets: encrypted`) and test with it.
  ```sh
  control-agent-server api POST /api/settings/mcp/qa-env --expect 201 \
    --json "$(jq -nc --argjson s "$TOOLS_SRV" '$s + {env: {QA_F19_TOKEN: "qa-f19-env-secret"}}')" \
    --check agent_settings.mcp_config.qa-env.env.QA_F19_TOKEN eq '**********'
  ENC=$(control-agent-server api GET /api/settings --header 'X-Expose-Secrets: encrypted' --field agent_settings.mcp_config.qa-env.env.QA_F19_TOKEN --unsafe-unredacted)
  test "${ENC#gAAAA}" != "$ENC"
  control-agent-server api POST /api/mcp/test --expect 200 --save F19.test-encrypted-env/decrypted \
    --json "$(jq -nc --argjson s "$TOOLS_SRV" --arg enc "$ENC" '{server: ($s + {env: {QA_F19_TOKEN: $enc}}), timeout: 30, tool_call: {name: "qa_env", arguments: {name: "QA_F19_TOKEN"}}}')" \
    --check ok eq true --check tool_result.text eq QA_ENV:qa-f19-env-secret
  control-agent-server api POST /api/mcp/test --expect 200 \
    --json "$(jq -nc --argjson s "$TOOLS_SRV" '{server: $s, timeout: 30, tool_call: {name: "qa_env", arguments: {name: "OH_SECRET_KEY"}}}')" \
    --check tool_result.text eq 'QA_ENV:<unset>'
  ```
  The child sees the decrypted value, not the ciphertext, and does not see the
  server's `OH_SECRET_KEY`. `qa-env` stays stored for `F19.disabled-server`.
- **Remote server behind a bearer token (`F19.test-remote-bearer`), known bug #6687.**
  ```sh
  control-agent-server api POST /api/mcp/test --expect 200 --save F19.test-remote-bearer/auth-bearer \
    --json "{\"name\":\"qa-http\",\"server\":{\"type\":\"http\",\"url\":\"http://127.0.0.1:$BEARER_PORT/mcp\",\"auth\":{\"strategy\":\"bearer\",\"value\":\"qa-f19-bearer\"}},\"timeout\":20,\"tool_call\":{\"name\":\"read_subject\",\"arguments\":{\"subject\":\"hi\"}}}" \
    --check ok eq true --check tools contains read_subject --check tool_result.text eq QA_HTTP_SUBJECT:hi
  control-agent-server api POST /api/mcp/test --expect 200 \
    --json "{\"server\":{\"type\":\"shttp\",\"url\":\"http://127.0.0.1:$BEARER_PORT/mcp\",\"headers\":{\"Authorization\":\"Bearer qa-f19-bearer\"}},\"timeout\":20}" \
    --check ok eq true --check tools contains read_subject
  control-agent-server api POST /api/mcp/test --expect 200 \
    --json "{\"server\":{\"transport\":\"http\",\"url\":\"http://127.0.0.1:$BEARER_PORT/mcp\",\"auth\":{\"strategy\":\"header\",\"headers\":{\"Authorization\":\"Bearer qa-f19-bearer\"}}},\"timeout\":20}" \
    --check ok eq true --check tools contains read_subject
  control-agent-server api POST /api/mcp/test --expect 200 --save F19.test-remote-bearer/wrong-token \
    --json "{\"server\":{\"transport\":\"http\",\"url\":\"http://127.0.0.1:$BEARER_PORT/mcp\",\"auth\":{\"strategy\":\"bearer\",\"value\":\"qa-f19-wrong\"}},\"timeout\":20}" \
    --check ok eq false --check error contains 401  # bug: status eq 200 && response.body.error contains 'error response'
  ```
  The right token works through `auth.strategy: bearer`, through a plain
  header (with the legacy `shttp` alias) and through `auth.strategy: header`
  (the form the 422 for `auth` plus an `Authorization` header recommends);
  the wrong one should answer `ok: false` naming the 401, but today the error
  is `MCPError: Server returned an error response` (`error_kind: unknown`) and
  the status is lost since the mcp 2 migration
  ([OpenHands/software-agent-sdk#6687](https://github.com/OpenHands/software-agent-sdk/issues/6687)).
- **OAuth start refusals and early outcomes (`F19.oauth-start-errors`).**
  ```sh
  control-agent-server api POST /api/mcp/oauth/start --json "$(jq -nc --argjson s "$MCP" '{server: $s}')" --expect 400 \
    --check detail eq "MCP OAuth start requires auth.strategy='oauth2'" --save F19.oauth-start-errors/stdio
  control-agent-server api POST /api/mcp/oauth/start --expect 400 \
    --json "{\"server\":{\"type\":\"http\",\"url\":\"http://127.0.0.1:$BEARER_PORT/mcp\",\"auth\":{\"strategy\":\"bearer\",\"value\":\"qa-f19-bearer\"}}}"
  JU=$(control-agent-server api POST /api/mcp/oauth/start --expect 200 --save F19.oauth-start-errors/unreachable \
    --json "{\"server\":{\"type\":\"http\",\"url\":\"http://127.0.0.1:$DEAD_PORT/mcp\",\"auth\":{\"strategy\":\"oauth2\"}},\"timeout\":5}" \
    --check ok eq false --check error_kind eq connection --check job_id exists --field job_id)
  control-agent-server api GET "/api/mcp/oauth/status/$JU" --check ok eq false --check status eq failed --check callback_ready eq false
  control-agent-server api POST "/api/mcp/oauth/callback/$JU" --json '{"callback_url":"http://localhost:1/callback?code=x"}' --expect 409 \
    --check detail eq 'OAuth callback is not ready'
  JN=$(control-agent-server api POST /api/mcp/oauth/start --expect 200 --save F19.oauth-start-errors/no-consent-needed \
    --json "{\"server\":{\"type\":\"http\",\"url\":\"http://127.0.0.1:$NONE_PORT/mcp\",\"auth\":{\"strategy\":\"oauth2\"}},\"timeout\":10}" \
    --check ok eq true --check authorization_url missing --field job_id)
  control-agent-server api GET "/api/mcp/oauth/status/$JN" --check status eq succeeded --check tools contains read_subject --check oauth_state missing --until-ok 10
  ```
  Stdio and bearer servers are 400. The unreachable server comes back
  immediately as `ok: false` with a job id whose status is `failed` and whose
  callback is 409. A server that needs no OAuth finishes at once: `ok: true`,
  a job id and no `authorization_url`, then `succeeded` with its tools.
- **Unknown jobs (`F19.oauth-unknown-job`).**
  ```sh
  control-agent-server api GET /api/mcp/oauth/status/qa-f19-nope --expect 404 --check detail eq 'MCP OAuth job not found' --save F19.oauth-unknown-job/status
  control-agent-server api POST /api/mcp/oauth/callback/qa-f19-nope --json '{"callback_url":"http://127.0.0.1:1/callback"}' --expect 404 \
    --check detail eq 'MCP OAuth job not found'
  control-agent-server api POST /api/mcp/oauth/callback/qa-f19-nope --json '{}' --expect 422
  ```
  Both lookups are 404 (not 500); the body is validated before the lookup.
- **Full OAuth consent (`F19.oauth-flow`).** The recipe plays the browser:
  it requests the authorization URL without following the redirect and hands
  the `Location` (the probe's `http://localhost:<port>/callback?code=...`) to
  the callback route.
  ```sh
  J=$(control-agent-server api POST /api/mcp/oauth/start --expect 200 --save F19.oauth-flow/start \
    --json "$(jq -nc --argjson s "$OAUTH_SRV" '{name: "qa-oauth", server: $s, timeout: 60, tool_call: {name: "read_subject", arguments: {subject: "hi"}}}')" \
    --check ok eq true --check authorization_url matches "^http://127.0.0.1:$OAUTH_PORT/authorize\\?" --field job_id)
  control-agent-server api GET "/api/mcp/oauth/status/$J" --check status eq authorizing --check callback_ready eq true --until-ok 20 --save F19.oauth-flow/authorizing
  AUTH_URL=$(control-agent-server api GET "/api/mcp/oauth/status/$J" --field authorization_url)
  CALLBACK=$(curl -s --noproxy '*' -o /dev/null -w '%{redirect_url}' "$AUTH_URL")
  test "${CALLBACK#http://localhost:}" != "$CALLBACK"
  control-agent-server api POST "/api/mcp/oauth/callback/$J" --json "$(jq -nc --arg u "$CALLBACK" '{callback_url: $u}')" --expect 200 --check ok eq true --save F19.oauth-flow/callback
  control-agent-server api GET "/api/mcp/oauth/status/$J" --until-ok 30 --save F19.oauth-flow/succeeded \
    --check status eq succeeded --check tools contains read_subject --check tool_result.text eq QA_HTTP_SUBJECT:hi \
    --check oauth_state.tokens.access_token matches '^gAAAA' --check oauth_state.tokens.refresh_token matches '^gAAAA' \
    --check oauth_state.client_info.client_id exists --check oauth_state.token_expires_at exists
  ```
  Start returns the provider's `/authorize` URL (dynamic client registration
  already happened); status becomes `authorizing` with `callback_ready: true`;
  after the callback the job is `succeeded` with `read_subject`, its result
  `QA_HTTP_SUBJECT:hi`, and Fernet-encrypted tokens (the run has
  `OH_SECRET_KEY`).
- **Replayed callback (`F19.oauth-callback-replay`).** Once the probe
  finished, its local callback server is closed. A second submit (or a submit
  after the browser already reached the callback itself) is not forwarded:
  the route answers 200 with the finished job's status. The first command
  shows the job already finished, the second the replay, and the last that
  the job is unchanged.

  Requires: `F19.oauth-flow`
  ```sh
  control-agent-server api GET "/api/mcp/oauth/status/$J" --check status eq succeeded --check callback_ready eq true
  RBODY=$(jq -nc --arg u "$CALLBACK" '{callback_url: $u}')
  control-agent-server api POST "/api/mcp/oauth/callback/$J" --json "$RBODY" --expect 200 --check status eq succeeded --save F19.oauth-callback-replay/replay
  control-agent-server api GET "/api/mcp/oauth/status/$J" --check status eq succeeded
  ```
  A callback submitted after a job failed on its consent timeout likewise
  answers the `failed` status; one that reaches a live job whose callback
  server is gone is 409 `OAuth callback server is not listening`.
- **Port out of range (`F19.oauth-callback-bad-port`).**
  `_validate_callback_url` reads `urlparse(...).port`, which raises
  `ValueError` for `99999` or a non-numeric port; the route turns that into
  the same 400 as any other port that does not match. The bullet uses its own
  job that is waiting for consent, so only the URL check is under test. The
  in-range wrong port is the control.
  ```sh
  JB=$(control-agent-server api POST /api/mcp/oauth/start --json "$(jq -nc --argjson s "$OAUTH_SRV" '{server: $s, timeout: 30}')" \
    --check authorization_url exists --field job_id)
  control-agent-server api GET "/api/mcp/oauth/status/$JB" --check callback_ready eq true --until-ok 20
  control-agent-server api POST "/api/mcp/oauth/callback/$JB" --json '{"callback_url":"http://localhost:1/callback?code=x"}' --expect 400 \
    --check detail eq 'Unexpected OAuth callback URL'
  control-agent-server api POST "/api/mcp/oauth/callback/$JB" --json '{"callback_url":"http://localhost:99999/callback?code=x"}' --expect 400 \
    --check detail eq 'Unexpected OAuth callback URL' --save F19.oauth-callback-bad-port/port-99999
  control-agent-server api POST "/api/mcp/oauth/callback/$JB" --json '{"callback_url":"http://localhost:abc/callback?code=x"}' --expect 400 \
    --check detail eq 'Unexpected OAuth callback URL' --save F19.oauth-callback-bad-port/port-abc
  control-agent-server api GET "/api/mcp/oauth/status/$JB" --check status eq authorizing
  ```
  Both posts answer 400 `Unexpected OAuth callback URL`, like port `0` and
  any other in-range port that does not match, and the job keeps waiting.
- **Callback URL checks on a live job (`F19.oauth-callback-validation`).** A
  fresh job, four wrong URLs, then the right one with `127.0.0.1`.
  ```sh
  JV=$(control-agent-server api POST /api/mcp/oauth/start --json "$(jq -nc --argjson s "$OAUTH_SRV" '{server: $s, timeout: 60}')" \
    --check authorization_url exists --field job_id)
  control-agent-server api GET "/api/mcp/oauth/status/$JV" --check callback_ready eq true --until-ok 20
  VCB=$(curl -s --noproxy '*' -o /dev/null -w '%{redirect_url}' "$(control-agent-server api GET "/api/mcp/oauth/status/$JV" --field authorization_url)")
  VPORT=$(echo "$VCB" | sed -E 's#^http://localhost:([0-9]+)/.*#\1#')
  control-agent-server api POST "/api/mcp/oauth/callback/$JV" --json "{\"callback_url\":\"https://localhost:$VPORT/callback?code=x\"}" --expect 400 \
    --check detail eq 'Invalid OAuth callback URL' --save F19.oauth-callback-validation/https
  control-agent-server api POST "/api/mcp/oauth/callback/$JV" --json "{\"callback_url\":\"http://qa-f19.example:$VPORT/callback?code=x\"}" --expect 400 \
    --check detail eq 'Invalid OAuth callback URL'
  control-agent-server api POST "/api/mcp/oauth/callback/$JV" --json "{\"callback_url\":\"http://localhost:$VPORT/qa-other?code=x\"}" --expect 400 \
    --check detail eq 'Unexpected OAuth callback URL'
  control-agent-server api POST "/api/mcp/oauth/callback/$JV" --json '{"callback_url":"http://localhost:1/callback?code=x"}' --expect 400 \
    --check detail eq 'Unexpected OAuth callback URL'
  control-agent-server api GET "/api/mcp/oauth/status/$JV" --check status eq authorizing
  VCB_IP=$(echo "$VCB" | sed 's#//localhost:#//127.0.0.1:#')
  VBODY=$(jq -nc --arg u "$VCB_IP" '{callback_url: $u}')
  control-agent-server api POST "/api/mcp/oauth/callback/$JV" --json "$VBODY" \
    --expect 200 --check ok eq true --save F19.oauth-callback-validation/loopback-ip
  control-agent-server api GET "/api/mcp/oauth/status/$JV" --check status eq succeeded --until-ok 30
  ```
  The scheme and host checks answer `Invalid OAuth callback URL`, the port
  and path checks `Unexpected OAuth callback URL`; none of them disturbs the
  waiting job, and the loopback IP form completes it.
- **Denied consent (`F19.oauth-consent-denied`).** The fixture's provider
  approves everything, so the recipe plays a provider that refused: it keeps
  the probe's callback port and `state` from the real redirect and replaces
  the code with `error=access_denied`, as a provider does when the user
  clicks "Deny".
  ```sh
  JDN=$(control-agent-server api POST /api/mcp/oauth/start --json "$(jq -nc --argjson s "$OAUTH_SRV" '{server: $s, timeout: 60}')" \
    --check authorization_url exists --field job_id)
  control-agent-server api GET "/api/mcp/oauth/status/$JDN" --check callback_ready eq true --until-ok 20
  DAU=$(control-agent-server api GET "/api/mcp/oauth/status/$JDN" --field authorization_url)
  DCB=$(curl -s --noproxy '*' -o /dev/null -w '%{redirect_url}' "$DAU")
  DSTATE=$(echo "$DCB" | sed -nE 's#.*[?&]state=([^&]*).*#\1#p')
  test -n "$DSTATE"
  DENY_URL="$(echo "$DCB" | sed 's#?.*##')?error=access_denied&error_description=QA%20denied&state=$DSTATE"
  DENY_BODY=$(jq -nc --arg u "$DENY_URL" '{callback_url: $u}')
  control-agent-server api POST "/api/mcp/oauth/callback/$JDN" --json "$DENY_BODY" --expect 200 --save F19.oauth-consent-denied/callback \
    --check ok eq false --check status eq failed --check error eq 'OAuth callback returned HTTP 400' --check error_kind eq connection
  control-agent-server api GET "/api/mcp/oauth/status/$JDN" --until-ok 15 --save F19.oauth-consent-denied/status \
    --check status eq failed --check ok eq false --check error contains 'Access was denied' --check oauth_state missing --check tools missing
  ```
  The callback route reports the probe's callback page answering 400; the job
  itself then fails with `Client failed to connect: Access was denied by the
  authorization server.` (`error_kind: connection`) and holds no tokens.
- **Consent timeout (`F19.oauth-consent-timeout`).** A job with a 4-second
  `timeout` whose authorization URL nobody visits; the probe's own timer is
  under test.
  ```sh
  T0=$SECONDS
  JT=$(control-agent-server api POST /api/mcp/oauth/start --json "$(jq -nc --argjson s "$OAUTH_SRV" '{server: $s, timeout: 4}')" \
    --check authorization_url exists --field job_id)
  control-agent-server api GET "/api/mcp/oauth/status/$JT" --check status eq authorizing
  control-agent-server api GET "/api/mcp/oauth/status/$JT" --until-ok 30 --save F19.oauth-consent-timeout/failed \
    --check status eq failed --check ok eq false --check error_kind eq timeout --check error contains 'timed out after 4.0 seconds'
  test $((SECONDS - T0)) -ge 4
  test $((SECONDS - T0)) -le 15
  ```
  The job waits in `authorizing`, then, between 4 and 15 seconds after start
  (about four), is `failed` with `error_kind: timeout`; the user would have to
  start over.
- **Redirect URI on the public origin (`F19.oauth-public-redirect`).**
  A browser that is not on the agent-server's host (a container that
  publishes only the API port, a reverse proxy) cannot reach
  `http://localhost:<port>/callback`. First this run, which has no external
  URL, then a second server launched with `OH_WEB_URL`; the recipe reads the
  `redirect_uri` the provider was asked to return to out of each
  `authorization_url`.
  ```sh
  PSTART=$(jq -nc --argjson s "$OAUTH_SRV" '{server: $s, timeout: 5}')
  LAU=$(control-agent-server api POST /api/mcp/oauth/start --json "$PSTART" --expect 200 --check ok eq true --field authorization_url)
  "$PY" -c "$QS_OF" "$LAU" | control-agent-server assert --check redirect_uri matches '^http://localhost:[0-9]+/callback$'
  stop_public() {
    control-agent-server stop --run "$PUB" >/dev/null
  }
  PUB=$(control-agent-server launch --new --name f19-public --env OH_WEB_URL=https://qa-f19.example/qa-f19 --print-run)
  trap stop_public EXIT
  control-agent-server config --run "$PUB" | jq -e '.server_env.OH_WEB_URL == "https://qa-f19.example/qa-f19"'
  PAU=$(control-agent-server api POST /api/mcp/oauth/start --run "$PUB" --json "$PSTART" --expect 200 --check ok eq true \
    --check authorization_url matches "^http://127.0.0.1:$OAUTH_PORT/authorize\\?" --save F19.oauth-public-redirect/start --field authorization_url)
  "$PY" -c "$QS_OF" "$PAU" | control-agent-server assert --check redirect_uri eq https://qa-f19.example/qa-f19/api/mcp/oauth/redirect
  control-agent-server stop --run "$PUB"
  trap - EXIT
  ```
  Without an external URL the redirect URI is the probe's loopback listener,
  `http://localhost:<port>/callback`, which a browser on the same machine
  reaches. With `OH_WEB_URL=https://qa-f19.example/qa-f19` it is exactly
  `https://qa-f19.example/qa-f19/api/mcp/oauth/redirect`: the public origin,
  its path prefix and the agent-server's redirect route, so the provider
  sends the browser back to a route the agent-server serves
  (`F19.oauth-web-url-precedence` delivers one). Both jobs end on their
  5-second consent timeout.
- **Redirect to the agent-server's own route (`F19.oauth-redirect-route`).**
  The browser-facing flow without `web_url`: the start body names this run's
  base URL as `callback_base_url` (with a trailing slash, which is dropped),
  and the recipe plays the browser in two hops: it reads the provider's
  `Location` without following it, then requests that URL with no session
  key, as a browser would.
  ```sh
  CBR="$RUN_URL/api/mcp/oauth/redirect"
  JP=$(control-agent-server api POST /api/mcp/oauth/start --expect 200 --save F19.oauth-redirect-route/start \
    --json "$(jq -nc --argjson s "$OAUTH_SRV" --arg cb "$RUN_URL/" '{server: $s, timeout: 60, callback_base_url: $cb, tool_call: {name: "read_subject", arguments: {subject: "pub"}}}')" \
    --check ok eq true --check authorization_url matches "^http://127.0.0.1:$OAUTH_PORT/authorize\\?" --field job_id)
  control-agent-server api GET "/api/mcp/oauth/status/$JP" --check status eq authorizing --check callback_ready eq true --until-ok 20 --save F19.oauth-redirect-route/authorizing
  PAU=$(control-agent-server api GET "/api/mcp/oauth/status/$JP" --field authorization_url)
  "$PY" -c "$QS_OF" "$PAU" | control-agent-server assert --check redirect_uri eq "$CBR" --check state exists
  PCB=$(curl -s --noproxy '*' -o /dev/null -w '%{redirect_url}' "$PAU")
  test "${PCB%%\?*}" = "$CBR"
  PQ=$("$PY" -c "$QS_OF" "$PCB")
  PCODE=$(echo "$PQ" | jq -r .code)
  PSTATE=$(echo "$PQ" | jq -r .state)
  control-agent-server api GET /api/mcp/oauth/redirect --auth none --query "code=$PCODE" --query "state=$PSTATE" --expect 200 \
    --check . contains '<h1>Authorization complete</h1>' --check-header content-type contains text/html --save F19.oauth-redirect-route/redirect
  control-agent-server api GET "/api/mcp/oauth/status/$JP" --until-ok 30 --save F19.oauth-redirect-route/succeeded \
    --check status eq succeeded --check tools contains read_subject --check tool_result.text eq QA_HTTP_SUBJECT:pub \
    --check oauth_state.tokens.access_token matches '^gAAAA' --check oauth_state.client_info.client_id exists
  control-agent-server api GET /api/mcp/oauth/redirect --auth none --query "code=$PCODE" --query "state=$PSTATE" --expect 409 \
    --check . contains 'This sign-in was already completed.' --save F19.oauth-redirect-route/replay
  control-agent-server api GET "/api/mcp/oauth/status/$JP" --check status eq succeeded
  ```
  The authorization URL asks the provider to return to
  `<run url>/api/mcp/oauth/redirect`, and the provider's `Location` points
  there with `code` and `state`. That request, without any key, answers 200
  with an HTML page titled "Authorization complete"; the job then exchanges
  the code itself and is `succeeded` with `read_subject`, its result and
  Fernet-encrypted tokens. Delivering the same redirect again answers 409
  "This sign-in was already completed." and the job is unchanged.
- **Unknown sign-ins on the redirect route (`F19.oauth-redirect-unknown-state`).**
  A job waiting on the redirect route, then requests that do not carry its
  `state`; the control is the provider's denial with the real `state`.
  ```sh
  JX=$(control-agent-server api POST /api/mcp/oauth/start --expect 200 --check ok eq true --field job_id \
    --json "$(jq -nc --argjson s "$OAUTH_SRV" --arg cb "$RUN_URL" '{server: $s, timeout: 60, callback_base_url: $cb}')")
  control-agent-server api GET "/api/mcp/oauth/status/$JX" --check callback_ready eq true --until-ok 20
  XQ=$("$PY" -c "$QS_OF" "$(curl -s --noproxy '*' -o /dev/null -w '%{redirect_url}' "$(control-agent-server api GET "/api/mcp/oauth/status/$JX" --field authorization_url)")")
  XCODE=$(echo "$XQ" | jq -r .code)
  XSTATE=$(echo "$XQ" | jq -r .state)
  test -n "$XCODE" && test -n "$XSTATE" && test "$XSTATE" != qa-f19-forged
  control-agent-server api GET /api/mcp/oauth/redirect --auth none --query "code=$XCODE" --query state=qa-f19-forged --expect 400 \
    --check . contains '<h1>Authorization failed</h1>' --check . contains 'This sign-in is unknown or has expired.' --save F19.oauth-redirect-unknown-state/forged-state
  control-agent-server api GET /api/mcp/oauth/redirect --auth none --query "code=$XCODE" --query state= --expect 400 \
    --check . contains 'This sign-in is unknown or has expired.'
  control-agent-server api GET /api/mcp/oauth/redirect --auth none --query "code=$XCODE" --expect 400 \
    --check . contains 'This sign-in is unknown or has expired.' --save F19.oauth-redirect-unknown-state/no-state
  control-agent-server api GET "/api/mcp/oauth/status/$JX" --check status eq authorizing --check oauth_state missing --save F19.oauth-redirect-unknown-state/still-waiting
  control-agent-server api GET /api/mcp/oauth/redirect --auth none --query error=access_denied --query 'error_description=QA denied' --query "state=$XSTATE" \
    --expect 400 --check . contains '<h1>Authorization failed</h1>' --check . contains 'The provider reported: QA denied' --save F19.oauth-redirect-unknown-state/denied
  control-agent-server api GET "/api/mcp/oauth/status/$JX" --until-ok 15 --save F19.oauth-redirect-unknown-state/failed \
    --check status eq failed --check ok eq false --check error eq 'Client failed to connect: OAuth authorization failed: QA denied' \
    --check error_kind eq connection --check oauth_state missing --check tools missing
  ```
  A forged, empty or missing `state` answers 400 with the "Authorization
  failed" page ("This sign-in is unknown or has expired."), and the job keeps
  waiting with no tokens. The same route recognizes the job's real `state`:
  a provider error there answers 400 "The provider reported: QA denied" and
  fails the job without tokens (`Client failed to connect: OAuth authorization
  failed: QA denied`, `error_kind: connection`).
- **Callback base URL validation (`F19.oauth-callback-base-url-validation`).**
  ```sh
  cbstart() { jq -nc --argjson s "$OAUTH_SRV" --arg cb "$1" '{server: $s, timeout: 3, callback_base_url: $cb}'; }
  control-agent-server api POST /api/mcp/oauth/start --json "$(cbstart "$RUN_URL/?qa=1")" --expect 422 \
    --check detail.0.loc contains callback_base_url --check detail.0.msg contains 'callback_base_url must be a plain http(s) base URL' --save F19.oauth-callback-base-url-validation/query
  control-agent-server api POST /api/mcp/oauth/start --json "$(cbstart "$RUN_URL/#qa")" --expect 422 \
    --check detail.0.msg contains 'callback_base_url must be a plain http(s) base URL' --save F19.oauth-callback-base-url-validation/fragment
  control-agent-server api POST /api/mcp/oauth/start --json "$(cbstart 'http://qa:pw@127.0.0.1:9')" --expect 422 \
    --check detail.0.msg contains 'callback_base_url must be a plain http(s) base URL' --save F19.oauth-callback-base-url-validation/userinfo
  control-agent-server api POST /api/mcp/oauth/start --json "$(cbstart 'ftp://127.0.0.1:9')" --expect 422 \
    --check detail.0.msg contains 'callback_base_url must be a plain http(s) base URL' --save F19.oauth-callback-base-url-validation/scheme
  control-agent-server api POST /api/mcp/oauth/start --json "$(cbstart 'http:///qa-f19')" --expect 422 \
    --check detail.0.msg contains 'callback_base_url must be a plain http(s) base URL'
  CAU=$(control-agent-server api POST /api/mcp/oauth/start --json "$(cbstart 'https://qa-f19.example/qa-f19/')" --expect 200 \
    --check ok eq true --save F19.oauth-callback-base-url-validation/accepted --field authorization_url)
  "$PY" -c "$QS_OF" "$CAU" | control-agent-server assert --check redirect_uri eq https://qa-f19.example/qa-f19/api/mcp/oauth/redirect
  ```
  Query, fragment, userinfo, a missing host and an `ftp` scheme are each 422
  on `callback_base_url` before any job starts; a plain https base with a
  path prefix and a trailing slash is accepted and becomes
  `https://qa-f19.example/qa-f19/api/mcp/oauth/redirect` (that job ends on
  its 3-second consent timeout).
- **`web_url` wins over the request (`F19.oauth-web-url-precedence`).** A
  second server with `OH_WEB_URL`; the start body also names this run's URL
  as `callback_base_url`. Translated: no reverse proxy serves
  `https://qa-f19.example`, so the recipe plays it and delivers the
  provider's redirect to the second server's own route.
  ```sh
  stop_public_cb() {
    control-agent-server stop --run "$PUBC" >/dev/null
  }
  PUBC=$(control-agent-server launch --new --name f19-public-cb --env OH_WEB_URL=https://qa-f19.example/qa-f19 --print-run)
  trap stop_public_cb EXIT
  WJ=$(control-agent-server api POST /api/mcp/oauth/start --run "$PUBC" --expect 200 --check ok eq true --save F19.oauth-web-url-precedence/start --field job_id \
    --json "$(jq -nc --argjson s "$OAUTH_SRV" --arg cb "$RUN_URL" '{server: $s, timeout: 60, callback_base_url: $cb, tool_call: {name: "read_subject", arguments: {subject: "web"}}}')")
  control-agent-server api GET "/api/mcp/oauth/status/$WJ" --run "$PUBC" --check callback_ready eq true --until-ok 20
  WAU=$(control-agent-server api GET "/api/mcp/oauth/status/$WJ" --run "$PUBC" --field authorization_url)
  "$PY" -c "$QS_OF" "$WAU" | control-agent-server assert --check redirect_uri eq https://qa-f19.example/qa-f19/api/mcp/oauth/redirect
  WCB=$(curl -s --noproxy '*' -o /dev/null -w '%{redirect_url}' "$WAU")
  test "${WCB%%\?*}" = https://qa-f19.example/qa-f19/api/mcp/oauth/redirect
  WQ=$("$PY" -c "$QS_OF" "$WCB")
  control-agent-server api GET /api/mcp/oauth/redirect --run "$PUBC" --auth none --query "code=$(echo "$WQ" | jq -r .code)" --query "state=$(echo "$WQ" | jq -r .state)" \
    --expect 200 --check . contains '<h1>Authorization complete</h1>' --save F19.oauth-web-url-precedence/redirect
  control-agent-server api GET "/api/mcp/oauth/status/$WJ" --run "$PUBC" --until-ok 30 --save F19.oauth-web-url-precedence/succeeded \
    --check status eq succeeded --check tool_result.text eq QA_HTTP_SUBJECT:web --check oauth_state.tokens.access_token matches '^gAAAA'
  control-agent-server stop --run "$PUBC"
  trap - EXIT
  ```
  The redirect URI is the `web_url` one, not `<run url>/api/mcp/oauth/redirect`
  from the body, and the provider's `Location` points at it. The second
  server's route takes the redirect without a key (200 "Authorization
  complete") and its job succeeds with `QA_HTTP_SUBJECT:web` and encrypted
  tokens. The evidence of the second server's calls is saved under that
  run's `evidence/F19.oauth-web-url-precedence/`.
- **Callback forwarding stops at the listener (`F19.oauth-callback-no-redirect`), known bug #6808.**
  Translated: the probe's own callback listener never redirects, so a
  stand-in takes its place. The job's `tool_call` keeps the job alive for 20
  seconds after consent (`qa_wait`); the browser delivers the code itself, the
  probe closes its listener, and a recipe-local server takes the freed port
  and answers every request with a 302 to an HTTP sink. A callback submitted
  then passes the URL checks (same port and path) and is forwarded.
  ```sh
  cat > "$F19/qa_f19_redirect.py" <<'EOF'
  """Answer every GET on 127.0.0.1:<port> with a 302 to <target>; append each request path to <hits>."""
  import http.server
  import sys
  import time

  port, target, hits = int(sys.argv[1]), sys.argv[2], sys.argv[3]


  class Handler(http.server.BaseHTTPRequestHandler):
      def do_GET(self) -> None:
          with open(hits, "a") as out:
              out.write(self.path + "\n")
          self.send_response(302)
          self.send_header("Location", target)
          self.send_header("Content-Length", "0")
          self.end_headers()

      def log_message(self, *args: object) -> None:
          pass


  for attempt in range(100):
      try:
          server = http.server.HTTPServer(("127.0.0.1", port), Handler)
          break
      except OSError:
          time.sleep(0.1)
  server.serve_forever()
  EOF
  RTARGET=$(control-agent-server fixture http-sink --name qa-f19-redirect-target | jq -r .url)
  JR=$(control-agent-server api POST /api/mcp/oauth/start --expect 200 --check ok eq true --field job_id \
    --json "$(jq -nc --argjson s "$OAUTH_SRV" '{server: $s, timeout: 60, tool_call: {name: "qa_wait", arguments: {seconds: 20}}}')")
  control-agent-server api GET "/api/mcp/oauth/status/$JR" --check callback_ready eq true --until-ok 20
  RCB=$(curl -s --noproxy '*' -o /dev/null -w '%{redirect_url}' "$(control-agent-server api GET "/api/mcp/oauth/status/$JR" --field authorization_url)")
  RPORT=${RCB#http://localhost:}
  RPORT=${RPORT%%/*}
  test "$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' "$RCB")" = 200
  control-agent-server helper start --name qa-f19-redirect --log "$F19/redirect.log" \
    -- "$PY" "$F19/qa_f19_redirect.py" "$RPORT" "$RTARGET/qa-f19-redirected" "$F19/redirect-hits" >/dev/null
  for i in $(seq 1 80); do test "$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' "http://127.0.0.1:$RPORT/qa-f19-up")" = 302 && break; sleep 0.25; done
  control-agent-server api GET "/api/mcp/oauth/status/$JR" --check status eq authorizing --check oauth_state missing
  RSUBMIT=$(jq -nc --arg u "http://127.0.0.1:$RPORT/callback?code=qa-f19-code&state=qa-f19-state" '{callback_url: $u}')
  control-agent-server api POST "/api/mcp/oauth/callback/$JR" --json "$RSUBMIT" \
    --expect 200 --check job_id eq "$JR" --save F19.oauth-callback-no-redirect/callback
  control-agent-server helper stop qa-f19-redirect >/dev/null
  test "$(grep -cx '/callback?code=qa-f19-code&state=qa-f19-state' "$F19/redirect-hits")" = 1
  control-agent-server sink read --name qa-f19-redirect-target --expect-max 0 --save F19.oauth-callback-no-redirect/redirect-target  # bug: requests eq 1
  control-agent-server api GET "/api/mcp/oauth/status/$JR" --check status eq succeeded --check tool_result.text eq QA_HTTP_WAITED --until-ok 40
  ```
  The stand-in at the job's callback address received the forwarded query
  exactly once, and the route answered 200 with the job's status. The sink the
  302 points at must have received nothing: only the address the URL checks
  accepted may be contacted. The bug: the forwarding client follows redirects,
  so the sink records one `GET /qa-f19-redirected`, a request to a destination
  no check ever saw. The job itself is not disturbed and succeeds with
  `QA_HTTP_WAITED` once the tool returns.
- **Inline OAuth server refresh (`F19.oauth-inline-writeback`).** The hosted
  case: the app server passes the OAuth server inline on the agent instead of
  storing it here. The recipe takes the token set `F19.oauth-callback-validation`
  obtained (job `JV`), marks it expired (`token_expires_at: 1`, a client-side
  value: the provider would still accept the token) and starts a conversation
  with a placeholder model and that server, queueing a prompt without running
  it so only the MCP connection happens. No server with this URL is in the
  settings yet.

  Requires: `F19.oauth-callback-validation`
  ```sh
  ISTATE=$(control-agent-server api GET "/api/mcp/oauth/status/$JV" --check oauth_state.tokens.refresh_token exists --field oauth_state --unsafe-unredacted)
  INLINE=$(jq -nc --argjson s "$OAUTH_SRV" --argjson st "$ISTATE" '$s | .auth.state = ($st | .token_expires_at = 1) | .transport = .type | del(.type)')
  IBODY=$(jq -nc --argjson srv "$INLINE" '{agent_settings: {agent_kind: "openhands", llm: {model: "openai/qa-placeholder", api_key: "qa-placeholder", base_url: "http://127.0.0.1:9/v1", num_retries: 0}, tools: [], mcp_config: {"qa-inline": $srv}}, secrets_encrypted: true}')
  control-agent-server sink read --path /mcp-oauth-state --expect-max 0
  ICID=$(control-agent-server conversation start --body-json "$IBODY" --no-autotitle --prompt 'Say hi' --no-run --print-id)
  control-agent-server api GET "/api/conversations/$ICID/events/search" --query limit=1 --max-chars 300 --until-ok 30 \
    --check items.0.kind eq SystemPromptEvent --check . contains read_subject --save F19.oauth-inline-writeback/system-prompt
  IW=$(control-agent-server sink read --path /mcp-oauth-state --contains "\"server_url\": \"http://127.0.0.1:$OAUTH_PORT/mcp\"" \
    --expect-min 2 --wait 20 --save F19.oauth-inline-writeback/webhook | jq -r .saved)
  jq -e --argjson now "$(date +%s)" '[.requests[] | select(.headers["X-Session-API-Key"] == "<redacted:session-api-key>")
    | .body.oauth_state | select(.tokens.access_token and .tokens.refresh_token and .client_info.client_id) | .token_expires_at] | max > $now' "$IW"
  control-agent-server api GET /api/settings --check agent_settings.mcp_config.qa-inline missing
  control-agent-server api DELETE "/api/conversations/$ICID" --expect 200
  ```
  The conversation lists `read_subject`; the webhook sink, empty for this
  path before, received `POST /mcp-oauth-state` for the server's URL, carrying
  this run's session key and an `oauth_state` with new tokens, the client
  registration and an expiry in the future (the first of the two posts still
  has the old expiry `1.0`; the second carries the refreshed one). Nothing
  was stored in the settings. The posted tokens are plaintext: the app server
  is expected to encrypt what it persists.
- **Stored OAuth state (`F19.oauth-stored-state`).** Reuse the state from
  `F19.oauth-flow` for a test, a start, and a stored server.

  Requires: `F19.oauth-flow`
  ```sh
  STATE=$(control-agent-server api GET "/api/mcp/oauth/status/$J" --field oauth_state --unsafe-unredacted)
  OSTATE_SRV=$(jq -nc --argjson s "$OAUTH_SRV" --argjson st "$STATE" '$s | .auth.state = $st')
  control-agent-server api POST /api/mcp/test --expect 200 --save F19.oauth-stored-state/test \
    --json "$(jq -nc --argjson s "$OSTATE_SRV" '{server: $s, timeout: 15, tool_call: {name: "read_subject", arguments: {subject: "again"}}}')" \
    --check ok eq true --check tool_result.text eq QA_HTTP_SUBJECT:again --check oauth_state.tokens.access_token exists
  JS=$(control-agent-server api POST /api/mcp/oauth/start --json "$(jq -nc --argjson s "$OSTATE_SRV" '{server: $s, timeout: 15}')" \
    --expect 200 --check ok eq true --check authorization_url missing --field job_id)
  control-agent-server api GET "/api/mcp/oauth/status/$JS" --check status eq succeeded --check tools contains read_subject --until-ok 10
  control-agent-server api POST /api/settings/mcp/qa-oauth --expect 201 --save F19.oauth-stored-state/settings-create \
    --json "$(jq -nc --argjson s "$OSTATE_SRV" '$s | .transport = .type | del(.type)')" \
    --check agent_settings.mcp_config.qa-oauth.auth.state.tokens.access_token eq '**********' \
    --check agent_settings.mcp_config.qa-oauth.auth.state.client_info.client_id exists
  TOKEN=$(control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' --field agent_settings.mcp_config.qa-oauth.auth.state.tokens.access_token --unsafe-unredacted)
  test -n "$TOKEN"
  test "${TOKEN#gAAAA}" = "$TOKEN"
  control-agent-server state cat home/.openhands/settings.json --not-contains "$TOKEN" \
    --check agent_settings.mcp_config.qa-oauth.auth.state.tokens.access_token matches '^gAAAA'
  ```
  The test and the start connect from the stored tokens without any
  authorization URL. Posting the encrypted state unchanged into the settings
  route stores the real token (plaintext GET is not ciphertext), redacted in
  responses and encrypted on disk.
- **Disabled servers stay off (`F19.disabled-server`).** Store the CLI's
  stdio fixture, disable `qa-env`, and look at the tools a new conversation
  offers its model. The conversation runs the settings' agent with its LLM
  pointed at an `llm-stub` that finishes at once (`--llm-json`), so the
  stub's recorded request is the tool list the model saw. The first
  `SystemPromptEvent` is not that list: it holds only the tools of servers
  that were ready within the 1-second startup budget
  (`F19.conversation-mcp-slow-start`), and the stdio fixture takes longer.

  Requires: `F19.test-encrypted-env`, `F19.oauth-stored-state`
  ```sh
  control-agent-server api POST /api/settings/mcp/qa-f19 --json "$MCP" --expect 201
  control-agent-server api PATCH /api/settings/mcp/qa-env --json '{"enabled":false}' --expect 200 --check agent_settings.mcp_config.qa-env.enabled eq false
  D_STUB=$(control-agent-server fixture llm-stub --name qa-f19-disabled-stub --step 'tool:finish:{"message":"QA_F19_DISABLED_DONE"}' --print-path)
  DCID=$(control-agent-server conversation start --tools none --no-autotitle --prompt 'Say hi' --print-id \
    --llm-json "$(jq -nc --arg b "$D_STUB/v1" '{model: "openai/qa-stub", api_key: "qa-stub-key", base_url: $b, num_retries: 0}')")
  control-agent-server conversation wait "$DCID" --until finished --timeout 90
  control-agent-server sink read --name qa-f19-disabled-stub --contains '"name": "qa-f19_qa_echo"' --expect-min 1 --save F19.disabled-server/model-request
  control-agent-server sink read --name qa-f19-disabled-stub --contains '"name": "qa-oauth_read_subject"' --expect-min 1
  control-agent-server sink read --name qa-f19-disabled-stub --contains qa_env --expect-max 0
  control-agent-server api GET "/api/conversations/$DCID/events/search" --query limit=1 --max-chars 300 --save F19.disabled-server/system-prompt \
    --check items.0.kind eq SystemPromptEvent --check . not-contains qa_env
  pgrep -fx "$MCP_CMDLINE"
  if pgrep -f "^$PY $F19/qa_f19_tools.py"; then echo 'the disabled server was spawned' >&2; false; fi
  control-agent-server api DELETE "/api/conversations/$DCID" --expect 200
  for i in $(seq 1 20); do pgrep -fx "$MCP_CMDLINE" >/dev/null || break; sleep 0.5; done
  if pgrep -fx "$MCP_CMDLINE"; then echo 'the stdio child outlived its conversation' >&2; false; fi
  ```
  The model's request offers `qa-f19_qa_echo` and `qa-oauth_read_subject`
  (two enabled servers, so names carry the server prefix) and nothing from
  `qa-env`; the enabled stdio server runs while the disabled one never
  starts, and deleting the conversation stops the child.
- **The agent uses the MCP tool (`F19.conversation-tool`).** A DeepSeek turn,
  captured live on the event socket.

  Requires: `F19.disabled-server`
  ```sh
  CID=$(control-agent-server conversation start --tools none --no-autotitle --no-run --print-id)
  control-agent-server ws start "/sockets/events/$CID" --name f19-live --duration 300
  control-agent-server conversation send "$CID" --text 'Call the MCP tool whose name ends with qa_echo, with text hi. Then finish.' --wait --until finished --timeout 240
  control-agent-server conversation events "$CID" --kinds ObservationEvent --contains QA_MCP_ECHO:hi --expect-kind ObservationEvent --save F19.conversation-tool/observation
  control-agent-server ws stop f19-live --kinds ObservationEvent --contains QA_MCP_ECHO:hi --expect-kind ObservationEvent --expect-min 1 --wait 15 --save F19.conversation-tool/ws-frames
  pgrep -fx "$MCP_CMDLINE"
  control-agent-server api DELETE "/api/conversations/$CID" --expect 200
  for i in $(seq 1 20); do pgrep -fx "$MCP_CMDLINE" >/dev/null || break; sleep 0.5; done
  if pgrep -fx "$MCP_CMDLINE"; then echo 'the stdio child outlived its conversation' >&2; false; fi
  ```
  The run finishes, an `ObservationEvent` (an `MCPToolObservation` for
  `qa-f19_qa_echo`) carries `QA_MCP_ECHO:hi` in the REST event list and as a
  WebSocket frame, the stdio child exists during the conversation and is gone
  after the delete.
- **Non-text tool results (`F19.conversation-tool`), known bug #6993.**
  The payload fixture as the agent's only server; an `llm-stub` model calls
  `qa_resource`, then `qa_structured_only`, and finishes.
  ```sh
  PL_STUB=$(control-agent-server fixture llm-stub --name qa-f19-pl-stub --step 'tool:qa_resource:{}' --step 'tool:qa_structured_only:{}' --step 'tool:finish:{"message":"QA_F19_PL_DONE"}' --print-path)
  PL_AGENT=$(jq -nc --arg b "$PL_STUB/v1" --argjson s "$PAYLOAD_SRV" '{llm: {model: "openai/qa-stub", api_key: "qa-stub-key", base_url: $b, num_retries: 0}, tools: [], mcp_config: {"qa-f19-payload": $s}}')
  PLCID=$(control-agent-server conversation start --body-json "$(jq -nc --argjson a "$PL_AGENT" '{agent: $a}')" --no-autotitle --prompt 'Read the payloads.' --wait --timeout 90 --print-id)
  control-agent-server conversation events "$PLCID" --kinds ObservationEvent --contains "Tool 'qa_structured_only' executed" --expect-kind ObservationEvent
  control-agent-server conversation events "$PLCID" --kinds ObservationEvent --contains QA_F19_STRUCTURED --expect-kind ObservationEvent --save F19.conversation-tool/structured-only # bug: matching eq 0 && kinds.ObservationEvent eq 3
  control-agent-server conversation events "$PLCID" --kinds ObservationEvent --contains QA_F19_RESOURCE_TEXT --expect-kind ObservationEvent --save F19.conversation-tool/resource # bug: matching eq 0 && kinds.ObservationEvent eq 3
  control-agent-server api DELETE "/api/conversations/$PLCID" --expect 200 --quiet
  ```
  Both calls ran (the `qa_structured_only` observation exists), and each
  observation should carry the tool's payload (both checks are marked; the
  first one stops the bullet today). Today the structured-only
  call's observation is only `[Tool 'qa_structured_only' executed.]`, and the
  resource call's only `[Tool 'qa_resource' executed.]`: the SDK's
  `MCPToolObservation.from_call_tool_result` keeps text and image blocks and
  never reads `structured_content`, so neither the model nor an event
  consumer sees the payload.
- **A server tool named like a built-in (`F19.conversation-tool`), known bug #6992.**
  The agent has the built-in `terminal`, and the payload fixture (which also
  advertises `terminal`) is its only MCP server, with its handshake delayed
  by 3 s so it always joins after the 1 s startup budget (see Gotchas): the
  path a slow-starting server takes, whatever the machine's load. The
  `llm-stub` model runs the built-in terminal, then `qa_sibling`.
  ```sh
  SH_STUB=$(control-agent-server fixture llm-stub --name qa-f19-sh-stub --step 'tool:terminal:{"command":"echo QA_F19_BUILTIN_TERMINAL"}' --step 'tool:qa_sibling:{}' --step 'tool:finish:{"message":"QA_F19_SH_DONE"}' --print-path)
  SH_AGENT=$(jq -nc --arg b "$SH_STUB/v1" --argjson s "$PAYLOAD_SRV" '{llm: {model: "openai/qa-stub", api_key: "qa-stub-key", base_url: $b, num_retries: 0}, tools: [{name: "terminal"}], mcp_config: {"qa-f19-shadow": ($s + {args: ($s.args + ["3"])})}}')
  SHCID=$(control-agent-server conversation start --body-json "$(jq -nc --argjson a "$SH_AGENT" '{agent: $a}')" --no-autotitle --print-id)
  trap 'control-agent-server api DELETE "/api/conversations/$SHCID" --expect 200,404 >/dev/null || true' EXIT
  control-agent-server api POST "/api/conversations/$SHCID/events" --json '{"role": "user", "content": [{"type": "text", "text": "Use both tools."}], "run": true}' \
    --expect 200 --save F19.conversation-tool/shadow-first-message
  control-agent-server conversation wait "$SHCID" --until finished --timeout 90
  control-agent-server conversation events "$SHCID" --kinds ObservationEvent --contains QA_F19_BUILTIN_TERMINAL --expect-kind ObservationEvent
  control-agent-server conversation events "$SHCID" --kinds ObservationEvent --contains QA_F19_SIBLING --expect-kind ObservationEvent \
    --save F19.conversation-tool/shadow-sibling # bug: matching eq 0 && kinds.AgentErrorEvent eq 1
  control-agent-server sink read --name qa-f19-sh-stub --contains '"name": "qa-f19-shadow_terminal"' --expect-min 1
  ```
  The built-in terminal runs (the control). The server's tools should join
  as `qa-f19-shadow_terminal` and `qa_sibling`, so the sibling call returns
  `QA_F19_SIBLING`. Today the late join is refused as a whole (the log says
  `Could not add MCP tools of qa-f19-shadow: Dynamically advertised MCP
  tools conflict with existing runtime tools: ['terminal']`), the model is
  offered no tool of that server, and the sibling call ends in an
  `AgentErrorEvent` `Tool 'qa_sibling' not found`; the run still finishes.
  A server that is ready within the budget hits the same collision during
  initialization instead, and the first message is 500 `Duplicate tool
  names found: {'terminal'}` with the conversation left `idle`; that
  variant depends on start-up speed, so the recipe does not drive it.
- **Stored OAuth token refresh (`F19.oauth-refresh-writeback`).** Expire the
  stored `qa-oauth` token with a sparse patch (client-side expiry only), then
  let a new conversation connect.

  Requires: `F19.oauth-stored-state`, `F19.oauth-inline-writeback`
  ```sh
  N0=$(control-agent-server sink read --path /mcp-oauth-state --expect-min 1 | jq .requests)
  control-agent-server api PATCH /api/settings/mcp/qa-oauth --json '{"auth":{"strategy":"oauth2","state":{"token_expires_at":1}}}' --expect 200 \
    --check agent_settings.mcp_config.qa-oauth.auth.state.tokens.access_token eq '**********'
  control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' --save F19.oauth-refresh-writeback/expired \
    --check agent_settings.mcp_config.qa-oauth.auth.state.token_expires_at eq 1 \
    --check agent_settings.mcp_config.qa-oauth.auth.state.tokens.access_token eq "$TOKEN" \
    --check agent_settings.mcp_config.qa-oauth.auth.state.tokens.refresh_token exists \
    --check agent_settings.mcp_config.qa-oauth.auth.state.client_info.client_id exists
  RCID=$(control-agent-server conversation start --tools none --no-autotitle --prompt 'Say hi' --no-run --print-id)
  control-agent-server api GET "/api/conversations/$RCID/events/search" --query limit=1 --max-chars 300 --until-ok 30 \
    --check items.0.kind eq SystemPromptEvent --check . contains read_subject
  control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' --until-ok 15 --save F19.oauth-refresh-writeback/refreshed \
    --check agent_settings.mcp_config.qa-oauth.auth.state.tokens.access_token ne "$TOKEN" \
    --check agent_settings.mcp_config.qa-oauth.auth.state.token_expires_at gt "$(date +%s)"
  TOKEN=$(control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' --field agent_settings.mcp_config.qa-oauth.auth.state.tokens.access_token --unsafe-unredacted)
  test -n "$TOKEN"
  control-agent-server state cat home/.openhands/settings.json --not-contains "$TOKEN" \
    --check agent_settings.mcp_config.qa-oauth.auth.state.tokens.access_token matches '^gAAAA' \
    --check agent_settings.mcp_config.qa-oauth.auth.state.tokens.refresh_token matches '^gAAAA'
  N1=$(control-agent-server sink read --path /mcp-oauth-state --save F19.oauth-refresh-writeback/webhook | jq .requests)
  test "$N1" -eq "$N0"
  control-agent-server api DELETE "/api/conversations/$RCID" --expect 200
  ```
  The patch keeps the tokens and client registration and changes only the
  expiry. The conversation still lists `read_subject`, and the stored server
  now holds a different access token with an expiry in the future, encrypted
  on disk; the webhook received nothing new, because the server is in the
  settings. `TOKEN` now names the rotated token, which `F19.restart` reads
  back.
- **Credentials at rest (`F19.conversation-secrets-at-rest`).**
  A new conversation from the current settings (`qa-f19`, disabled `qa-env` with its `env` secret, `qa-oauth` with
  its tokens), then a search of everything the server wrote.

  Requires: `F19.oauth-refresh-writeback`, `F19.disabled-server`
  ```sh
  SCID=$(control-agent-server conversation start --tools none --no-autotitle --prompt 'Say hi' --no-run --print-id)
  control-agent-server api GET "/api/conversations/$SCID/events/search" --query limit=1 --max-chars 300 --until-ok 30 \
    --check items.0.kind eq SystemPromptEvent --check . contains read_subject
  control-agent-server state cat base_state.json --conversation "$SCID" --max-chars 300 \
    --check agent.mcp_config.qa-env.enabled eq false \
    --check agent.mcp_config.qa-env.env.QA_F19_TOKEN matches '^gAAAA' \
    --check agent.mcp_config.qa-oauth.auth.state.tokens.access_token matches '^gAAAA' \
    --check agent.mcp_config.qa-oauth.auth.state.tokens.refresh_token matches '^gAAAA'
  test "$(control-agent-server state grep qa-oauth --glob 'server/workspace/conversations/**/*' | jq .matches)" -ge 1
  control-agent-server state grep qa-f19-env-secret --glob 'server/workspace/conversations/**/*' --expect-none
  control-agent-server state grep "$TOKEN" --glob 'server/workspace/conversations/**/*' --expect-none
  control-agent-server state grep qa-f19-env-secret --glob 'home/**/*' --expect-none
  control-agent-server logs --grep 'MCP OAuth authorization URL' --expect-min 1
  control-agent-server logs --grep 'qa-f19-env-secret|qa-f19-t0k|qa-f19-x|qa-f19-sib|qa-f19-bearer|qa-f19-wrong|test_access_token|test_refresh_token' --expect-none
  control-agent-server api DELETE "/api/conversations/$SCID" --expect 200
  ```
  The conversation's `base_state.json` keeps the agent's `mcp_config` with
  every credential as Fernet ciphertext (the disabled server included), the
  plaintext env secret and access token are found nowhere under the
  conversations directory or the run's home (while the server key `qa-oauth`
  is, so the search sees those files), and none of the header, env, bearer or
  OAuth token values used in this family reached the server
  log (which does hold the OAuth job lines).
- **Restart (`F19.restart`).** Settings are durable, OAuth jobs are not.

  Requires: `F19.oauth-flow`, `F19.oauth-refresh-writeback`, `F19.disabled-server`
  ```sh
  control-agent-server restart
  control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' --save F19.restart/after-restart \
    --check agent_settings.mcp_config.qa-env.env.QA_F19_TOKEN eq qa-f19-env-secret \
    --check agent_settings.mcp_config.qa-env.enabled eq false \
    --check agent_settings.mcp_config.qa-oauth.auth.state.tokens.access_token eq "$TOKEN" \
    --check agent_settings.mcp_config.qa-f19.command eq "$PY"
  control-agent-server exec --expect-output 'SDK_MCP_ENV_OK=True' --save F19.restart/sdk -- "$PY" "$F19/sdk_mcp_config.py"
  control-agent-server api GET "/api/mcp/oauth/status/$J" --expect 404 --check detail eq 'MCP OAuth job not found'
  control-agent-server helper stop qa-f19-http-bearer qa-f19-http-oauth qa-f19-http-none >/dev/null
  for key in qa-f19 qa-env qa-oauth; do control-agent-server api DELETE "/api/settings/mcp/$key" --expect 200 >/dev/null; done
  control-agent-server api GET /api/settings --check agent_settings.mcp_config.qa-oauth missing --check agent_settings.mcp_config.qa-f19 missing
  ```
  After the restart the env secret, the disabled flag and the OAuth token read
  back unchanged (also through `RemoteWorkspace.get_mcp_config()`), the
  finished job id is 404, and the bullet removes the fixtures and the three
  servers.
- **Add a server to a running conversation (`F19.conversation-mcp-add`).**
  A conversation whose agent is initialized (a queued first message), with
  an `llm-stub` model that calls the added tool and finishes; the CLI's
  stdio fixture is added under the name `qa-f19-rt`.
  ```sh
  control-agent-server api GET /server_info --save F19.conversation-mcp-add/server-info \
    --check capabilities contains conversation_mcp_install_v1 --check capabilities contains conversation_configuration_additions_v1
  RT_STUB=$(control-agent-server fixture llm-stub --name qa-f19-rt-stub --step 'tool:qa-f19-rt_qa_echo:{"text":"added"}' --step 'tool:finish:{"message":"QA_F19_RT_DONE"}' --print-path)
  RT_AGENT=$(jq -nc --arg b "$RT_STUB/v1" '{llm: {model: "openai/qa-stub", api_key: "qa-stub-key", base_url: $b, num_retries: 0}, tools: []}')
  RT_MCP=$(control-agent-server fixture mcp-server --name qa-f19-rt | jq -c .mcp_server)
  RT_CMDLINE=$(echo "$RT_MCP" | jq -r '[.command] + .args | join(" ")')
  ACID=$(control-agent-server conversation start --body-json "$(jq -nc --argjson a "$RT_AGENT" '{agent: $a}')" --no-autotitle --prompt 'Use the added tool.' --no-run --print-id)
  control-agent-server api GET "/api/conversations/$ACID" --check agent.mcp_config.qa-f19-rt missing --quiet
  if pgrep -fx "$RT_CMDLINE"; then echo 'spawned before the add' >&2; false; fi
  control-agent-server api POST "/api/conversations/$ACID/mcp" --json "$(jq -nc --argjson s "$RT_MCP" '{mcp_servers: {"qa-f19-rt": $s}}')" \
    --expect 200 --check success eq true --save F19.conversation-mcp-add/add
  pgrep -fx "$RT_CMDLINE"
  control-agent-server api GET "/api/conversations/$ACID" --check agent.mcp_config.qa-f19-rt.command eq "$PY" --check agent.mcp_config.qa-f19-rt.enabled eq true --save F19.conversation-mcp-add/get
  control-agent-server state cat base_state.json --conversation "$ACID" --max-chars 300 --check agent.mcp_config.qa-f19-rt.command eq "$PY"
  control-agent-server api POST "/api/conversations/$ACID/run" --expect 200 --quiet
  control-agent-server conversation wait "$ACID" --until finished --timeout 90
  control-agent-server sink read --name qa-f19-rt-stub --contains '"name": "qa-f19-rt_qa_echo"' --expect-min 2 --save F19.conversation-mcp-add/model-requests
  control-agent-server conversation events "$ACID" --kinds ObservationEvent --contains QA_MCP_ECHO:added --expect-kind ObservationEvent --save F19.conversation-mcp-add/observation
  ```
  The add answers only after the child runs; the agent, its persisted state
  and the model's next request all have the server (`qa-f19-rt_qa_echo`),
  and the stubbed call returns `QA_MCP_ECHO:added`. The conversation stays
  for the error bullet, which deletes it.
- **Add before the first message (`F19.conversation-mcp-add-before-init`).**
  A conversation created without a message has no initialized agent. The
  added server is the F19 tools fixture with an `env` value encrypted by the
  settings API (read back from a short-lived settings entry).
  ```sh
  control-agent-server api POST /api/settings/mcp/qa-f19-rt-enc --json '{"command":"/bin/true","env":{"QA_F19_RT":"qa-f19-rt-secret"}}' --expect 201 --quiet
  RT_ENC=$(control-agent-server api GET /api/settings --header 'X-Expose-Secrets: encrypted' --field agent_settings.mcp_config.qa-f19-rt-enc.env.QA_F19_RT --unsafe-unredacted)
  control-agent-server api DELETE /api/settings/mcp/qa-f19-rt-enc --expect 200 --quiet
  test "${RT_ENC#gAAAA}" != "$RT_ENC"
  BI_STUB=$(control-agent-server fixture llm-stub --name qa-f19-bi-stub --step 'tool:qa_env:{"name":"QA_F19_RT"}' --step 'tool:finish:{"message":"QA_F19_BI_DONE"}' --print-path)
  BI_AGENT=$(jq -nc --arg b "$BI_STUB/v1" '{llm: {model: "openai/qa-stub", api_key: "qa-stub-key", base_url: $b, num_retries: 0}, tools: []}')
  BCID=$(control-agent-server conversation start --body-json "$(jq -nc --argjson a "$BI_AGENT" '{agent: $a}')" --no-autotitle --print-id)
  control-agent-server api POST "/api/conversations/$BCID/mcp" --expect 200 --expect-max-ms 1000 --save F19.conversation-mcp-add-before-init/add \
    --json "$(jq -nc --argjson s "$TOOLS_SRV" --arg enc "$RT_ENC" '{mcp_servers: {"qa-f19-bi": ($s + {env: {QA_F19_RT: $enc}})}}')"
  if pgrep -f "^$PY $F19/qa_f19_tools.py"; then echo 'spawned before the first message' >&2; false; fi
  control-agent-server state cat base_state.json --conversation "$BCID" --max-chars 300 --check agent.mcp_config.qa-f19-bi.env.QA_F19_RT matches '^gAAAA'
  control-agent-server state grep qa-f19-rt-secret --glob 'server/workspace/conversations/**/*' --expect-none
  control-agent-server conversation send "$BCID" --text 'Read the variable.' --wait --until finished --timeout 90
  control-agent-server sink read --name qa-f19-bi-stub --contains '"name": "qa_env"' --expect-min 2
  control-agent-server conversation events "$BCID" --kinds ObservationEvent --contains QA_ENV:qa-f19-rt-secret --expect-kind ObservationEvent --save F19.conversation-mcp-add-before-init/observation
  control-agent-server api DELETE "/api/conversations/$BCID" --expect 200 --quiet
  ```
  The add answers within a second and spawns nothing; the stored server
  keeps the env value as ciphertext. The first message connects it (one
  server, so the tool is the bare `qa_env`) and the child sees the
  decrypted value.
- **Refused additions (`F19.conversation-mcp-add-errors`).**

  Requires: `F19.conversation-mcp-add`
  ```sh
  NOPE=$("$PY" -c 'import uuid; print(uuid.uuid4())')
  control-agent-server api POST "/api/conversations/$NOPE/mcp" --json '{"mcp_servers":{"qa-f19-nope":{"command":"/bin/true"}}}' --expect 404
  control-agent-server api POST "/api/conversations/$ACID/mcp" --json '{"mcp_servers":{"qa-f19-rt":{"command":"/bin/true"}}}' \
    --expect 400 --check detail contains 'MCP server already exists' --save F19.conversation-mcp-add-errors/duplicate
  control-agent-server api POST "/api/conversations/$ACID/mcp" --json '{"mcp_servers":{"qa-f19-sse":{"url":"http://127.0.0.1:9/sse","transport":"sse"}}}' \
    --expect 400 --check detail eq 'Runtime MCP additions do not support SSE transport: qa-f19-sse'
  control-agent-server api POST "/api/conversations/$ACID/mcp" --json '{"mcp_servers":{"qa-f19-bad":{}}}' --expect 422
  control-agent-server api POST "/api/conversations/$ACID/mcp" --json '{"mcp_servers":{"qa-f19-bad":{"command":"/bin/true","bogus":1}}}' --expect 422
  control-agent-server api POST /api/conversations/qa-f19-not-a-uuid/mcp --json '{"mcp_servers":{}}' --expect 422
  OFF_MARK="$F19/qa-f19-off-started"
  OFF_BODY=$(jq -nc --arg c "touch $OFF_MARK" '{mcp_servers: {"qa-f19-off": {command: "/bin/sh", args: ["-c", $c], enabled: false}}}')
  control-agent-server api POST "/api/conversations/$ACID/mcp" --expect 200 --json "$OFF_BODY"
  control-agent-server api GET "/api/conversations/$ACID" --save F19.conversation-mcp-add-errors/after \
    --check agent.mcp_config.qa-f19-rt.command eq "$PY" --check agent.mcp_config.qa-f19-off.enabled eq false \
    --check agent.mcp_config.qa-f19-sse missing --check agent.mcp_config.qa-f19-bad missing --check agent.mcp_config.qa-f19-nope missing
  test ! -e "$OFF_MARK"
  control-agent-server api DELETE "/api/conversations/$ACID" --expect 200 --quiet
  for i in $(seq 1 20); do pgrep -fx "$RT_CMDLINE" >/dev/null || break; sleep 0.5; done
  if pgrep -fx "$RT_CMDLINE"; then echo 'the added stdio child outlived its conversation' >&2; false; fi
  ```
  Each refusal leaves the agent's `mcp_config` as it was; the disabled
  server is stored with `enabled: false` and its command never runs; deleting
  the conversation stops the child the add started.
- **A server that cannot start (`F19.conversation-mcp-add-start-failure`).**
  The command exits at once, so the MCP connection closes during the
  handshake. The SDK raises `MCPServerStartError` and the route answers its
  documented refusal, 400 (`Invalid MCP server update`).
  ```sh
  SF_AGENT='{"llm": {"model": "openai/qa-offline", "api_key": "qa-offline-key", "base_url": "http://127.0.0.1:9/v1", "num_retries": 0}, "tools": []}'
  FCID=$(control-agent-server conversation start --body-json "$(jq -nc --argjson a "$SF_AGENT" '{agent: $a}')" --no-autotitle --prompt 'Never run.' --no-run --print-id)
  control-agent-server api POST "/api/conversations/$FCID/mcp" --json '{"mcp_servers":{"qa-f19-dies":{"command":"/bin/true"}}}' --expect 400 \
    --check detail eq 'Could not start MCP server qa-f19-dies: Connection closed' --save F19.conversation-mcp-add-start-failure/add
  control-agent-server api GET "/api/conversations/$FCID" --max-chars 300 --check agent.mcp_config.qa-f19-dies missing --save F19.conversation-mcp-add-start-failure/after
  ```
  The 400's `detail` names the server and the cause, and the server is not
  added (`agent.mcp_config` keeps no `qa-f19-dies`).
- **Added servers after a restart (`F19.conversation-mcp-add-restart`), known bug #6927.**
  The stub calls the added tool, finishes, and after a restart and a new
  message calls the same tool again.
  ```sh
  RS_STUB=$(control-agent-server fixture llm-stub --name qa-f19-rs-stub --step 'tool:qa-f19-rs_qa_echo:{"text":"one"}' \
    --step 'tool:finish:{"message":"QA_F19_RS_ONE"}' --step 'tool:qa-f19-rs_qa_echo:{"text":"two"}' --step 'tool:finish:{"message":"QA_F19_RS_TWO"}' --print-path)
  RS_AGENT=$(jq -nc --arg b "$RS_STUB/v1" '{llm: {model: "openai/qa-stub", api_key: "qa-stub-key", base_url: $b, num_retries: 0}, tools: []}')
  RS_MCP=$(control-agent-server fixture mcp-server --name qa-f19-rs | jq -c .mcp_server)
  RCID=$(control-agent-server conversation start --body-json "$(jq -nc --argjson a "$RS_AGENT" '{agent: $a}')" --no-autotitle --prompt 'First.' --no-run --print-id)
  control-agent-server api POST "/api/conversations/$RCID/mcp" --json "$(jq -nc --argjson s "$RS_MCP" '{mcp_servers: {"qa-f19-rs": $s}}')" --expect 200 --quiet
  control-agent-server api POST "/api/conversations/$RCID/run" --expect 200 --quiet
  control-agent-server conversation wait "$RCID" --until finished --timeout 90
  control-agent-server conversation events "$RCID" --kinds ObservationEvent --contains QA_MCP_ECHO:one --expect-kind ObservationEvent
  control-agent-server restart
  control-agent-server api GET "/api/conversations/$RCID" --check agent.mcp_config.qa-f19-rs.command eq "$PY" --save F19.conversation-mcp-add-restart/after-restart
  control-agent-server conversation send "$RCID" --text 'Again.' --wait --until finished --timeout 90
  control-agent-server conversation events "$RCID" --kinds ObservationEvent --contains QA_MCP_ECHO:two --expect-kind ObservationEvent --save F19.conversation-mcp-add-restart/second-call # bug: matching eq 0 && kinds.AgentErrorEvent ge 1
  ```
  The server survives the restart in the conversation's agent and is
  reconnected on the next message, but as the agent's only server its tools
  come back bare (`qa_echo`), so the second call by the name the agent was
  given ends in an `AgentErrorEvent` `Tool 'qa-f19-rs_qa_echo' not found`.
- **A slow server does not hold up the start (`F19.conversation-mcp-slow-start`).**
  The F19 tools fixture with a handshake delayed by 8 s, inline on the
  conversation's agent; the stub reads one variable through it.
  ```sh
  SL_STUB=$(control-agent-server fixture llm-stub --name qa-f19-slow-stub --step 'tool:qa_env:{"name":"QA_F19_SLOW"}' --step 'tool:finish:{"message":"QA_F19_SLOW_DONE"}' --print-path)
  SL_AGENT=$(jq -nc --arg b "$SL_STUB/v1" --argjson s "$TOOLS_SRV" '{llm: {model: "openai/qa-stub", api_key: "qa-stub-key", base_url: $b, num_retries: 0}, tools: [], mcp_config: {"qa-f19-slow": ($s + {args: ($s.args + ["8"]), env: {QA_F19_SLOW: "qa-f19-slow-ok"}})}}')
  T0=$SECONDS
  SLCID=$(control-agent-server conversation start --body-json "$(jq -nc --argjson a "$SL_AGENT" '{agent: $a}')" --no-autotitle --prompt 'Read the variable.' --print-id)
  test $((SECONDS - T0)) -le 5
  pgrep -f "^$PY $F19/qa_f19_tools.py 8"
  control-agent-server conversation wait "$SLCID" --until finished --timeout 120
  test $((SECONDS - T0)) -ge 8
  control-agent-server sink read --name qa-f19-slow-stub --contains '"name": "qa_env"' --expect-min 2 --save F19.conversation-mcp-slow-start/model-requests
  control-agent-server conversation events "$SLCID" --kinds ObservationEvent --contains QA_ENV:qa-f19-slow-ok --expect-kind ObservationEvent --save F19.conversation-mcp-slow-start/observation
  control-agent-server api DELETE "/api/conversations/$SLCID" --expect 200 --quiet
  ```
  `POST /api/conversations` returns while the server is still sleeping, the
  run's first model request comes only after the server is ready and
  already offers `qa_env`, and the call returns the server's environment
  value.
- **A late server that fails (`F19.conversation-mcp-late-failure`).** Two
  servers inline: the CLI's stdio fixture and a command that sleeps 6 s and
  exits before any handshake.
  ```sh
  LF_STUB=$(control-agent-server fixture llm-stub --name qa-f19-late-stub --step 'tool:finish:{"message":"QA_F19_LATE_DONE"}' --print-path)
  LF_AGENT=$(jq -nc --arg b "$LF_STUB/v1" --argjson ok "$MCP" '{llm: {model: "openai/qa-stub", api_key: "qa-stub-key", base_url: $b, num_retries: 0}, tools: [], mcp_config: {"qa-f19-ok": $ok, "qa-f19-dies-late": {command: "/bin/sh", args: ["-c", "sleep 6; exit 3"]}}}')
  T0=$SECONDS
  LCID=$(control-agent-server conversation start --body-json "$(jq -nc --argjson a "$LF_AGENT" '{agent: $a}')" --no-autotitle --prompt 'Finish.' --print-id)
  test $((SECONDS - T0)) -le 4
  control-agent-server conversation wait "$LCID" --until finished --timeout 90
  test $((SECONDS - T0)) -ge 6
  control-agent-server sink read --name qa-f19-late-stub --contains '"name": "qa-f19-ok_qa_echo"' --expect-min 1 --save F19.conversation-mcp-late-failure/model-request
  control-agent-server conversation events "$LCID" --kinds ObservationEvent --contains QA_F19_LATE_DONE --expect-kind ObservationEvent
  control-agent-server logs --grep 'qa-f19-dies-late;' --expect-min 1
  control-agent-server api DELETE "/api/conversations/$LCID" --expect 200 --quiet
  ```
  The start returns before the failing server gives up, the first step
  waits for it, the model is offered the working server's prefixed tool,
  the run finishes, and the log carries `MCP server startup failed for
  qa-f19-dies-late; continuing without its tools: Connection closed`
  (wrapped over several lines, so the recipe greps `qa-f19-dies-late;`).

## Gotchas

- `POST /api/mcp/test` and OAuth start answer HTTP 200 for every connection
  problem: assert `ok` and `error_kind`, never the status code alone. A
  wrong bearer token (known bug #6687: `MCPError: Server returned an error response`, no HTTP status) and a command that exits at
  once (`McpError: Connection closed`) are classified `unknown`, not
  `connection`.
- `tool_call` runs the tool for real and shares the request `timeout`; pick a
  read-only tool. `ok` reflects only connect and list.
- Stdio children get the MCP SDK's default environment (`HOME`, `PATH`, ...)
  plus `env`, nothing from the agent server; use an absolute interpreter path.
  The CLI's fixture uses the checkout's `.venv` python; on a checkout without
  `.venv` the fixture command is `uv run ...` and this family's
  `test -x "$PY"` precondition fails.
- `pgrep -f` with a plain substring also matches the `bash -c` that runs the
  recipe (its text contains the pattern): match the whole command line with
  `pgrep -fx`, or anchor with `^`.
- A conversation connects to its MCP servers lazily: `POST /api/conversations`
  without `initial_message` spawns nothing and records no events; the first
  message (even queued with `run: false`) initializes the agent and spawns
  the stdio children.
- Conversation start waits at most 1 second (`MCP_STARTUP_BUDGET_SECONDS`)
  for MCP servers. The `SystemPromptEvent` written then lists only the tools
  of servers ready by that time and is never updated; servers still starting
  (a Python FastMCP stdio server usually takes 1-3 s, so the CLI fixtures
  do) join the agent when ready, and the first model step waits up to 30 s
  for them. To see what the model is offered, read an `llm-stub`'s recorded
  request (`sink read --name <stub> --contains '"name": "<tool>"'`), not the
  first event. Tools added through `POST /api/conversations/{id}/mcp` are not
  in it either.
- Tools of a server added through the conversation route are always named
  `<server>_<tool>` while the agent is running, but a restart (or an add
  before the first message) names them like configured servers: bare when it
  is the agent's only server (`F19.conversation-mcp-add-restart`).
- The conversation MCP route runs the connection inside the request: an
  initialized conversation answers only after the server has started (or
  failed, up to 30 s). Explicit `transport: sse` is refused, but a `/sse` URL
  without `transport` is not inferred as SSE here and is tried as HTTP.
- With two or more enabled servers, conversation tool names carry the server
  key as a prefix (`qa-f19_qa_echo`); with one server they are bare
  (`qa_echo`). Prompts and `--contains` checks should not depend on which.
- An unreachable server costs a conversation only its own tools, and so does
  a server that fails validation: it is skipped with `Skipping invalid MCP
  server <key>; continuing without its tools` (wrapped over several log lines,
  so grep for `Skipping invalid MCP server`). `MCP server startup failed for
  <keys>` still names every enabled key when MCP startup fails as a whole,
  for example when no server is valid.
- The settings routes use `transport` (`extra="forbid"`, so the test route's
  legacy `type` is 422 there). A trailing slash on the key path redirects
  (307) and an empty key is 404.
- Sending the redacted placeholder `**********` back in a `PATCH` deletes
  that header or env entry instead of keeping it. Round-trip credentials with
  `X-Expose-Secrets: encrypted` values (as `F19.test-encrypted-env` does) or
  leave them out of the patch.
- OAuth jobs live in process memory for 15 minutes and are lost on restart.
  `callback_ready` turns true slightly after `authorization_url` appears;
  submitting earlier is 409, so poll status with `--until-ok` first.
- When playing the browser, read the redirect `Location` without following it
  (`curl -w '%{redirect_url}'`). Following it delivers the code to the probe
  directly, completes the job, and a later callback POST only reports the
  finished job (`F19.oauth-callback-replay`). The whole consent, including the user's
  sign-in, must finish within the request's `timeout` (default 15 s, at most
  120 s) or the job fails with `error_kind: timeout`.
- The probe's callback listener closes as soon as it has received a code,
  while the job stays live until the token exchange, the tool listing and
  the `tool_call` are done. In that window the callback route still forwards
  a submitted URL to the freed port, to whatever listens there
  (`F19.oauth-callback-no-redirect`), or answers 409 `OAuth callback server
  is not listening`.
- The redirect URI is `{web_url}/api/mcp/oauth/redirect` when the server
  has `OH_WEB_URL` (whatever the start body says), else
  `{callback_base_url}/api/mcp/oauth/redirect` for a dynamically registered
  client, else FastMCP's loopback listener
  `http://localhost:<random port>/callback`. Only the loopback form goes
  through the callback route; the redirect route is reached by the browser
  itself, needs no session key and identifies the job by `state` alone, so a
  recipe extracts `code` and `state` from the provider's `Location` and
  passes them as `--query` with `--auth none`. It answers HTML (400 for an
  unknown, empty or missing `state` or a provider error, 409 for a second
  delivery), so assert with `--check . contains`.
- `oauth_state` is Fernet-encrypted when the server has `OH_SECRET_KEY`
  (every launched run does) and plaintext otherwise; posting it unchanged into
  `auth.state` of the settings route is the intended way to persist it.
- `auth.state.token_expires_at` is the client's view of the expiry: a past
  value makes the next connection refresh the token even though the provider
  would still accept it, which is how the refresh recipes trigger a refresh.
  A sparse `PATCH` of `{"auth":{"strategy":"oauth2","state":{"token_expires_at":1}}}`
  deep-merges and keeps the tokens and client registration. The refresh
  rotates both tokens (the fixture provider revokes the old ones), so a
  recipe that compares the stored token must re-read it afterwards.
- Refreshed tokens are written to the settings server whose `url` equals the
  connection's URL; a server passed inline on the agent matches only when no
  stored server has the same URL, and its refreshed state goes to the
  webhooks (`/mcp-oauth-state`, two posts per refresh: tokens, then expiry)
  in plaintext. Without a webhook in the config it lives only in memory for
  that conversation.
- The HTTP fixtures are recipe-local `helper`s, not CLI fixtures:
  `helper start` runs each in its own session and registers it with the run,
  so it outlives the Preconditions bullet (a plain `cmd &` would end with
  that bullet's process group). The last bullet's `helper stop` or
  `control-agent-server stop` ends them, and each also exits after 25
  minutes or 60 seconds after the run's `/alive` stops answering; their logs
  are under `fixtures/f19/`.
- Agent Canvas sends `callback_base_url` on OAuth start ([OpenHands/OpenHands#19744](https://github.com/OpenHands/OpenHands/pull/19744)), but bundles Agent Server 1.62.0 by default, which ignores the field and keeps the loopback redirect; the public redirect (`F19.oauth-redirect-route`) reaches Canvas users once Canvas pins a release that has it.
