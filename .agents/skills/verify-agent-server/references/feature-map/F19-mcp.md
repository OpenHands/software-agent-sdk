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
passed inline on the agent, posted to the app server's webhook.

Source: `openhands-agent-server/openhands/agent_server/mcp_router.py`, `openhands-agent-server/openhands/agent_server/mcp_oauth_store.py`, `openhands-agent-server/openhands/agent_server/settings_router.py`, `openhands-agent-server/openhands/agent_server/_secrets_exposure.py`, `openhands-sdk/openhands/sdk/mcp/`, `openhands-sdk/openhands/sdk/settings/api_models.py`, `openhands-sdk/openhands/sdk/utils/redact.py`, `openhands-sdk/openhands/sdk/workspace/remote/base.py`, `clients/typescript/src/client/mcp-client.ts`, `clients/typescript/src/client/settings-client.ts`

Needs: `llm`

Launch: `--webhook-sink`

Routes: `POST /api/mcp/test`, `POST /api/mcp/oauth/start`,
`GET /api/mcp/oauth/status/{job_id}`, `POST /api/mcp/oauth/callback/{job_id}`,
`POST /api/settings/mcp/{settings_key}`, `PATCH /api/settings/mcp/{settings_key}`,
`DELETE /api/settings/mcp/{settings_key}`

## Sub-features

- `F19.auth`: every F19 route answers 401 without or with a wrong session key and stores or spawns nothing.
- `F19.settings-create`: `POST /api/settings/mcp/{settings_key}` stores one server (201, the whole redacted `SettingsResponse`), `GET /api/settings` shows it redacted, `X-Expose-Secrets: plaintext` returns the credentials, the file on disk holds them encrypted, and creating an existing key is 409 without changing it.
- `F19.settings-validation`: creating a stdio server without `command`, a remote server without `url`, an unknown field or transport, or `auth` next to an `Authorization` header is 422 and stores nothing.
- `F19.settings-empty-server`: a server body with neither `command` nor `url` (`{}`) is rejected with 422 instead of being stored.
- `F19.settings-patch`: `PATCH` merges sparsely (`enabled: false` keeps url and headers, a `null` header entry deletes only that entry, `enabled: null` restores `true`); an unknown key is 404, an unknown field or a merge that leaves an http server without `url` is 422.
- `F19.settings-delete`: `DELETE` removes one server and leaves the others and their credentials intact; deleting a missing key is 404.
- `F19.test-stdio`: `POST /api/mcp/test` spawns a stdio server, lists its tools, runs a requested `tool_call` and returns its text, and no stdio child survives the response.
- `F19.test-tool-errors`: a tool that raises, an unadvertised tool name and a tool that outlives the timeout are reported in `tool_result` with `is_error: true` while `ok` stays `true`.
- `F19.test-connect-failures`: a missing executable, a closed port and a command that exits at once answer 200 with `ok: false`, an `error` and an `error_kind`.
- `F19.test-timeout`: a stdio server that never completes the handshake yields `ok: false`, `error_kind: timeout` close to the requested `timeout`, and its process is killed.
- `F19.test-validation`: a missing server, an empty command, an unknown transport, a `timeout` outside (0, 120], top-level `authentication` and `auth` next to an `Authorization` header are 422.
- `F19.test-encrypted-env`: `env` values encrypted by the settings API are decrypted before the stdio server starts, and the child does not inherit the agent server's own keys.
- `F19.test-remote-bearer`: a streamable-HTTP server behind a bearer token connects with `auth.strategy: bearer`, an `Authorization` header (also as `type: shttp`) or `auth.strategy: header`, and a wrong token answers `ok: false`.
- `F19.oauth-start-errors`: OAuth start is 400 for a stdio or non-oauth2 server, reports `ok: false` with a failed job for an unreachable server (whose callback is then 409), and finishes without consent for a server that needs no OAuth.
- `F19.oauth-unknown-job`: status and callback for an unknown job id are 404, and a callback without `callback_url` is 422.
- `F19.oauth-flow`: against a local OAuth-protected MCP server, start returns an `authorization_url`, status reports `authorizing` and `callback_ready`, and submitting the browser's redirect URL completes the job with tools, the tool result and an encrypted `oauth_state`.
- `F19.oauth-callback-replay`: submitting a callback again after the job finished answers the job's status or a client error (4xx), not 500, and the job stays `succeeded`.
- `F19.oauth-callback-bad-port`: a callback URL with a port outside 0-65535 is rejected with 400, not 500.
- `F19.oauth-callback-validation`: on a live job, an https URL, a foreign host, another port or another path is 400 and the job keeps waiting; the redirect URL with `127.0.0.1` instead of `localhost` completes it.
- `F19.oauth-consent-denied`: submitting the redirect a provider sends when the user denies consent (`error=access_denied`) answers `ok: false` and fails the job without tokens.
- `F19.oauth-consent-timeout`: a consent that is not completed within the job's `timeout` fails the job with `error_kind: timeout`.
- `F19.oauth-inline-writeback`: an OAuth server passed inline on a conversation's agent (not in settings) whose access token has expired is refreshed when the conversation connects, keeps its tools, and the rotated tokens are posted with the run's session key to the webhook's `/mcp-oauth-state`, not stored in settings.
- `F19.oauth-stored-state`: the returned `oauth_state` used as `auth.state` connects without consent (test, and start without `authorization_url`), and stored through the settings route it is redacted, encrypted on disk and decrypted to the real token.
- `F19.disabled-server`: a conversation started from the settings exposes the tools of enabled servers (including the OAuth server from its stored tokens) and neither exposes nor spawns a disabled server.
- `F19.conversation-tool`: a model-driven conversation calls the configured stdio server's `qa_echo` tool, the `ObservationEvent` (REST and WebSocket) carries `QA_MCP_ECHO:hi`, and deleting the conversation stops the stdio child.
- `F19.oauth-refresh-writeback`: when a stored OAuth server's access token has expired, the next conversation refreshes it, keeps the server's tools, and writes the rotated token and new expiry back into the settings (encrypted on disk) without posting them to the webhook.
- `F19.conversation-secrets-at-rest`: a conversation started from the settings persists its agent's MCP credentials (a disabled server's `env`, OAuth tokens) encrypted, and no MCP credential this family used appears in plaintext under the conversations directory or in the server log.
- `F19.restart`: stored MCP servers and their credentials survive a restart (plaintext GET and the SDK's `RemoteWorkspace.get_mcp_config()`), while in-memory OAuth jobs are gone (404).

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
- The CLI's stdio fixture (`fixture mcp-server`, tool `qa_echo`) plus three
  recipe-local fixtures written below (no CLI verb exists for them): a stdio
  server with `qa_env`, `qa_sleep` and `qa_fail` whose first argument delays
  the MCP handshake, an HTTP FastMCP server in three modes (`none`, `bearer`
  with the static token `qa-f19-bearer`, and `oauth` with FastMCP's
  `InMemoryOAuthProvider`, which approves every authorization request), and an
  SDK program. The three HTTP servers run in the background on free ports; each
  exits after 25 minutes or 60 seconds after the run's `/alive` stops
  answering, and the last bullet kills them.
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

  from mcp.server.fastmcp import FastMCP

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
  cat > "$F19/qa_f19_http.py" <<'EOF'
  """F19 HTTP MCP fixture: argv = port, mode (none|bearer|oauth), ttl seconds, run /alive URL."""
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
  RUN_URL=$(control-agent-server config | jq -r .url)
  free_port() { "$PY" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }
  BEARER_PORT=$(free_port)
  OAUTH_PORT=$(free_port)
  NONE_PORT=$(free_port)
  DEAD_PORT=$(free_port)
  nohup "$PY" "$F19/qa_f19_http.py" "$BEARER_PORT" bearer 1500 "$RUN_URL/alive" </dev/null >"$F19/http-bearer.log" 2>&1 &
  BEARER_PID=$!
  nohup "$PY" "$F19/qa_f19_http.py" "$OAUTH_PORT" oauth 1500 "$RUN_URL/alive" </dev/null >"$F19/http-oauth.log" 2>&1 &
  OAUTH_PID=$!
  nohup "$PY" "$F19/qa_f19_http.py" "$NONE_PORT" none 1500 "$RUN_URL/alive" </dev/null >"$F19/http-none.log" 2>&1 &
  NONE_PID=$!
  for port in "$BEARER_PORT" "$OAUTH_PORT" "$NONE_PORT"; do
    for i in $(seq 1 80); do curl -s --noproxy '*' -o /dev/null "http://127.0.0.1:$port/mcp" && break; sleep 0.25; done
  done
  test "$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' "http://127.0.0.1:$BEARER_PORT/mcp")" = 401
  test "$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' "http://127.0.0.1:$OAUTH_PORT/mcp")" = 401
  test "$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' "http://127.0.0.1:$NONE_PORT/mcp")" != 000
  OAUTH_SRV=$(jq -nc --arg url "http://127.0.0.1:$OAUTH_PORT/mcp" '{type: "http", url: $url, auth: {strategy: "oauth2", authentication: {type: "oauth", client_auth_method: "none", scopes: ["mail.read"]}}}')
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
- **Server with neither command nor url (`F19.settings-empty-server`), known bug.**
  `MCPServer` only requires `command` or `url` once `transport` is set,
  so `{}` is stored as `{"enabled": true}` with 201. While it exists, every new
  conversation's MCP configuration fails validation as a whole and the agent
  gets no MCP tools at all, only a `MCP server startup failed ... continuing
  without its tools` warning in the log (reproduced with a valid stdio server
  next to it). The correct behavior asserted is 422; on failure the same line
  removes the stored entry before failing, so later bullets are unaffected.
  ```sh
  control-agent-server api GET /api/settings --check agent_settings.mcp_config.qa-empty missing
  control-agent-server api POST /api/settings/mcp/qa-empty --json '{}' --expect 422 --save F19.settings-empty-server/create || { control-agent-server api DELETE /api/settings/mcp/qa-empty --expect 200,404 >/dev/null; false; }  # bug
  control-agent-server api GET /api/settings --check agent_settings.mcp_config.qa-empty missing
  ```
  Today the POST answers 201 with `qa-empty: {"enabled": true}` in the
  returned settings, the entry is deleted again and the bullet reports
  `xfail` at the `# bug` line.
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
  ENC=$(control-agent-server api GET /api/settings --header 'X-Expose-Secrets: encrypted' --field agent_settings.mcp_config.qa-env.env.QA_F19_TOKEN)
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
- **Remote server behind a bearer token (`F19.test-remote-bearer`).**
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
    --check ok eq false --check error contains 401
  ```
  The right token works through `auth.strategy: bearer`, through a plain
  header (with the legacy `shttp` alias) and through `auth.strategy: header`
  (the form the 422 for `auth` plus an `Authorization` header recommends);
  the wrong one answers `ok: false` naming the 401.
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
- **Replayed callback (`F19.oauth-callback-replay`), known bug.** Once the
  probe finished, its local callback server is closed; the route forwards the
  URL with `httpx` without handling the connection error, so a second submit
  (or a submit after the browser already reached the callback itself) is a 500
  `All connection attempts failed`. The first command shows the job already
  finished; the `# bug` line asserts the correct behavior, a handled answer
  (the job's status, or a 4xx such as 409), and the last that the job is
  unchanged.
  ```sh
  control-agent-server api GET "/api/mcp/oauth/status/$J" --check status eq succeeded --check callback_ready eq true
  RBODY=$(jq -nc --arg u "$CALLBACK" '{callback_url: $u}')
  control-agent-server api POST "/api/mcp/oauth/callback/$J" --json "$RBODY" --expect 200,4xx --save F19.oauth-callback-replay/replay  # bug
  control-agent-server api GET "/api/mcp/oauth/status/$J" --check status eq succeeded
  ```
  Today the `# bug` line gets 500 `All connection attempts failed`. The same
  500 (with an empty message) is returned for a callback submitted after a
  job failed on its consent timeout.
- **Port out of range (`F19.oauth-callback-bad-port`), known bug.**
  `_validate_callback_url` reads `urlparse(...).port`, which raises
  `ValueError` for `99999` or a non-numeric port; the route lets it escape as
  500. The bullet uses its own job that is waiting for consent, so only the
  URL check is under test. The in-range wrong port is the positive control
  (400 `Unexpected OAuth callback URL`); the two `# bug` lines assert the
  correct behavior, 400 like every other malformed URL, with the job still
  waiting.
  ```sh
  JB=$(control-agent-server api POST /api/mcp/oauth/start --json "$(jq -nc --argjson s "$OAUTH_SRV" '{server: $s, timeout: 30}')" \
    --check authorization_url exists --field job_id)
  control-agent-server api GET "/api/mcp/oauth/status/$JB" --check callback_ready eq true --until-ok 20
  control-agent-server api POST "/api/mcp/oauth/callback/$JB" --json '{"callback_url":"http://localhost:1/callback?code=x"}' --expect 400 \
    --check detail eq 'Unexpected OAuth callback URL'
  control-agent-server api POST "/api/mcp/oauth/callback/$JB" --json '{"callback_url":"http://localhost:99999/callback?code=x"}' --expect 400 --save F19.oauth-callback-bad-port/port-99999  # bug
  control-agent-server api POST "/api/mcp/oauth/callback/$JB" --json '{"callback_url":"http://localhost:abc/callback?code=x"}' --expect 400 --save F19.oauth-callback-bad-port/port-abc  # bug
  control-agent-server api GET "/api/mcp/oauth/status/$JB" --check status eq authorizing
  ```
  Today both `# bug` posts are 500 (`Port out of range 0-65535`,
  `Port could not be cast to integer value as 'abc'`) although the job keeps
  waiting; port `0` and other in-range ports already get the 400
  `Unexpected OAuth callback URL`.
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
- **Inline OAuth server refresh (`F19.oauth-inline-writeback`).** The hosted
  case: the app server passes the OAuth server inline on the agent instead of
  storing it here. The recipe takes the token set `F19.oauth-callback-validation`
  obtained (job `JV`), marks it expired (`token_expires_at: 1`, a client-side
  value: the provider would still accept the token) and starts a conversation
  with a placeholder model and that server, queueing a prompt without running
  it so only the MCP connection happens. No server with this URL is in the
  settings yet.
  ```sh
  ISTATE=$(control-agent-server api GET "/api/mcp/oauth/status/$JV" --check oauth_state.tokens.refresh_token exists --field oauth_state)
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
  ```sh
  STATE=$(control-agent-server api GET "/api/mcp/oauth/status/$J" --field oauth_state)
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
  TOKEN=$(control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' --field agent_settings.mcp_config.qa-oauth.auth.state.tokens.access_token)
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
  stdio fixture, disable `qa-env`, and look at a new conversation's tools
  without running the model: the conversation starts with a queued prompt
  (`--no-run`), because the agent, and with it every MCP connection, is
  initialized on the first message, not at creation.
  ```sh
  control-agent-server api POST /api/settings/mcp/qa-f19 --json "$MCP" --expect 201
  control-agent-server api PATCH /api/settings/mcp/qa-env --json '{"enabled":false}' --expect 200 --check agent_settings.mcp_config.qa-env.enabled eq false
  DCID=$(control-agent-server conversation start --tools none --no-autotitle --prompt 'Say hi' --no-run --print-id)
  control-agent-server api GET "/api/conversations/$DCID" --check execution_status eq idle
  control-agent-server api GET "/api/conversations/$DCID/events/search" --query limit=1 --max-chars 300 --save F19.disabled-server/system-prompt \
    --check items.0.kind eq SystemPromptEvent --check . contains qa_echo --check . contains read_subject --check . not-contains qa_env
  pgrep -fx "$MCP_CMDLINE"
  if pgrep -f "^$PY $F19/qa_f19_tools.py"; then echo 'the disabled server was spawned' >&2; false; fi
  control-agent-server api DELETE "/api/conversations/$DCID" --expect 200
  for i in $(seq 1 20); do pgrep -fx "$MCP_CMDLINE" >/dev/null || break; sleep 0.5; done
  if pgrep -fx "$MCP_CMDLINE"; then echo 'the stdio child outlived its conversation' >&2; false; fi
  ```
  The first event lists `qa-f19_qa_echo` and `qa-oauth_read_subject` (two
  enabled servers, so names carry the server prefix) and nothing from
  `qa-env`; the enabled stdio server runs while the disabled one never
  starts, and deleting the conversation stops the child.
- **The agent uses the MCP tool (`F19.conversation-tool`).** A DeepSeek turn,
  captured live on the event socket.
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
- **Stored OAuth token refresh (`F19.oauth-refresh-writeback`).** Expire the
  stored `qa-oauth` token with a sparse patch (client-side expiry only), then
  let a new conversation connect.
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
  TOKEN=$(control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' --field agent_settings.mcp_config.qa-oauth.auth.state.tokens.access_token)
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
  ```sh
  control-agent-server restart
  control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' --save F19.restart/after-restart \
    --check agent_settings.mcp_config.qa-env.env.QA_F19_TOKEN eq qa-f19-env-secret \
    --check agent_settings.mcp_config.qa-env.enabled eq false \
    --check agent_settings.mcp_config.qa-oauth.auth.state.tokens.access_token eq "$TOKEN" \
    --check agent_settings.mcp_config.qa-f19.command eq "$PY"
  control-agent-server exec --expect-output 'SDK_MCP_ENV_OK=True' --save F19.restart/sdk -- "$PY" "$F19/sdk_mcp_config.py"
  control-agent-server api GET "/api/mcp/oauth/status/$J" --expect 404 --check detail eq 'MCP OAuth job not found'
  kill "$BEARER_PID" "$OAUTH_PID" "$NONE_PID" 2>/dev/null || true
  for key in qa-f19 qa-env qa-oauth; do control-agent-server api DELETE "/api/settings/mcp/$key" --expect 200 >/dev/null; done
  control-agent-server api GET /api/settings --check agent_settings.mcp_config.qa-oauth missing --check agent_settings.mcp_config.qa-f19 missing
  ```
  After the restart the env secret, the disabled flag and the OAuth token read
  back unchanged (also through `RemoteWorkspace.get_mcp_config()`), the
  finished job id is 404, and the bullet removes the fixtures and the three
  servers.

## Gotchas

- `POST /api/mcp/test` and OAuth start answer HTTP 200 for every connection
  problem: assert `ok` and `error_kind`, never the status code alone. A
  wrong bearer token (`HTTPStatusError ... 401`) and a command that exits at
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
  message (even queued with `run: false`) initializes the agent, spawns the
  stdio children and writes the `SystemPromptEvent` with the MCP tools.
- With two or more enabled servers, conversation tool names carry the server
  key as a prefix (`qa-f19_qa_echo`); with one server they are bare
  (`qa_echo`). Prompts and `--contains` checks should not depend on which.
- An unreachable server costs a conversation only its own tools, but a
  configuration that fails validation as a whole (the empty server of
  `F19.settings-empty-server`) drops every MCP tool with only a log warning
  (`MCP server startup failed for <every key>; continuing without its tools`,
  wrapped over several log lines, so grep for `MCP server startup`).
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
  directly, completes the job, and a later callback POST hits the
  `F19.oauth-callback-replay` 500. The whole consent, including the user's
  sign-in, must finish within the request's `timeout` (default 15 s, at most
  120 s) or the job fails with `error_kind: timeout`.
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
- The HTTP fixtures are recipe-local background processes, not CLI fixtures:
  `control-agent-server stop` does not stop them. They exit after 25 minutes,
  60 seconds after the run's `/alive` stops answering, or when the last bullet
  kills them; their logs are under `fixtures/f19/`.
