# Bash commands and bash events

Out-of-band shell commands that a consumer (Agent Canvas terminal tabs, the
SDK's `RemoteWorkspace`, automations) runs on the server without going
through the agent: they never appear in the conversation's event stream and
the model never sees them. Each command is a `BashCommand` event followed by
one or more `BashOutput` events (stdout and stderr cut into 1 MiB chunks, the
last one carrying `exit_code`), all persisted as one JSON file per event and
searchable afterwards. There are two scopes with separate histories: the
global `/api/bash/...` service (cwd defaults to the server process cwd, any
cwd allowed, live frames on `WS /sockets/bash-events`) and the
conversation-scoped `/api/conversations/{id}/bash/...` service (cwd defaults
to the conversation workspace and must stay inside it, history stored with
the conversation, no socket).

Source: `openhands-agent-server/openhands/agent_server/bash_router.py`, `openhands-agent-server/openhands/agent_server/bash_service.py`, `openhands-agent-server/openhands/agent_server/runtime_router.py`, `openhands-agent-server/openhands/agent_server/dependencies.py`, `openhands-agent-server/openhands/agent_server/sockets.py`, `openhands-agent-server/openhands/agent_server/models.py`, `openhands-sdk/openhands/sdk/utils/command.py`, `openhands-sdk/openhands/sdk/workspace/remote/remote_workspace_mixin.py`, `clients/typescript/src/client/bash-client.ts`, `clients/typescript/src/events/bash-websocket-client.ts`

Routes: `GET /api/conversations/{runtime_conversation_id}/bash/bash_events/search`,
`GET /api/conversations/{runtime_conversation_id}/bash/bash_events/{event_id}`,
`GET /api/conversations/{runtime_conversation_id}/bash/bash_events/`,
`POST /api/conversations/{runtime_conversation_id}/bash/start_bash_command`,
`POST /api/conversations/{runtime_conversation_id}/bash/execute_bash_command`,
`DELETE /api/conversations/{runtime_conversation_id}/bash/bash_events`,
`POST /api/conversations/{runtime_conversation_id}/bash/bash_commands/{command_id}/stop`,
`GET /api/bash/bash_events/search`, `GET /api/bash/bash_events/{event_id}`,
`GET /api/bash/bash_events/`, `POST /api/bash/start_bash_command`,
`POST /api/bash/execute_bash_command`, `DELETE /api/bash/bash_events`,
`POST /api/bash/bash_commands/{command_id}/stop`, `WS /sockets/bash-events`

## Sub-features

- `F13.auth`: every bash route answers 401 without or with a wrong session key (and runs nothing); `WS /sockets/bash-events` closes with 4001 on a bad key.
- `F13.execute-sync`: `POST /api/bash/execute_bash_command` blocks until the command exits and returns its final `BashOutput` with the real exit code and separate stdout and stderr; a body without `command` or with a non-integer `timeout` is 422.
- `F13.global-cwd`: global commands run in the server process cwd by default, accept any explicit cwd, and a cwd that does not exist yields 200 with `exit_code` -1 and an `Error executing command` stderr.
- `F13.env-sanitized`: commands run without `OH_SESSION_API_KEYS_*`, `SESSION_API_KEY` and `OH_SECRET_KEY` in their environment and with `AI_AGENT=openhands`.
- `F13.env-config-credentials`: only those three names are stripped: other credential-bearing configuration variables (`OH_TELEMETRY_HTTP_TOKEN`, `OH_WEBHOOKS_0_HEADERS`) reach commands unchanged.
- `F13.start-async`: `POST /api/bash/start_bash_command` returns the `BashCommand` before the command finishes; its `BashOutput` with `exit_code` appears later in search.
- `F13.ws-live`: `WS /sockets/bash-events` streams `BashCommand` and `BashOutput` frames for commands started over REST, and `resend_mode=all` replays stored events on connect.
- `F13.ws-command`: a client frame shaped like `ExecuteBashRequest` starts a command on the socket; an invalid frame gets a `BashError` frame and the socket keeps working.
- `F13.ws-resend-all`: the deprecated `resend_all=true` query parameter replays stored events like `resend_mode=all` (and logs a deprecation warning), while a connection without either replays nothing.
- `F13.ws-subscriber-cap`: `WS /sockets/bash-events` holds at most 50 subscribers: with 50 open, the 51st is closed with 1013 `Too many bash event connections`, and closing one frees the slot.
- `F13.get-events`: `GET /api/bash/bash_events/{event_id}` returns one event by its hex id (404 when unknown), and `GET /api/bash/bash_events/` with a JSON array body returns events in input order with `null` for missing ids (422 without a body).
- `F13.search`: search filters by `kind__eq`, `command_id__eq` (outputs only), a UTC `timestamp__gte`/`timestamp__lt` window, sorts with `sort_order`, pages with `limit`/`next_page_id`, and rejects bad parameters with 422.
- `F13.timestamp-offset`: a `timestamp__gte` with a non-UTC offset selects the same instant as its UTC spelling.
- `F13.output-chunks`: output over 1 MiB is stored as ordered `BashOutput` chunks of 1048576 characters plus a final chunk with the exit code; `order__gt` skips seen chunks, and the SDK's `RemoteWorkspace.execute_command` reassembles them.
- `F13.order-gt-paging`: `order__gt` filters before `limit` applies, so a poller asking for outputs after order N with a small page gets them.
- `F13.execute-large-output`: `execute_bash_command` returns the complete stdout of a command whose output is over 1 MiB.
- `F13.timeout`: a command that outlives its `timeout` is killed with its process group and reports `exit_code` -1.
- `F13.stop`: stopping a running command by id terminates only that command (final `exit_code` -15, process gone) while another command finishes normally.
- `F13.stop-idempotent`: stopping an already finished command (hex or dashed id) returns 200, an unknown id 404 and a non-UUID id 422.
- `F13.scoped-cwd`: conversation-scoped commands run in the conversation workspace by default, accept an absolute cwd inside it (also through the SDK's `runtime_conversation_id`), and reject a cwd outside it, an escaping `..` path or a relative path with 422.
- `F13.scoped-routes`: the conversation-scoped execute, start, stop, get, batch get and search routes work on the conversation's own history, and the command's files land in the workspace.
- `F13.scoped-isolation`: scoped events are invisible to the global search, the global socket, the global stop route and other conversations, and are stored under the conversation's directory.
- `F13.scoped-unknown`: every conversation-scoped bash route answers 404 `Conversation not found` for an unknown conversation id.
- `F13.persist-restart`: global and conversation-scoped bash history survives a server restart.
- `F13.clear`: `DELETE .../bash_events` deletes one scope's stored events and reports `cleared_count`; the other scope is untouched, and a cleared finished command can no longer be stopped (404).
- `F13.retention`: with `bash_events_retention_seconds` set, global events older than the window are purged at startup while conversation-scoped history stays.
- `F13.retention-rolling`: the retention loop keeps purging while the server runs: a global event written after startup is readable at once and gone after the next pass (about 60 s) with no restart, while a conversation-scoped event stays.
- `F13.scoped-bad-id`: a conversation-scoped bash route with a non-UUID conversation id answers 422.
- `F13.scoped-idle-eviction`: a conversation with a running scoped command is not evicted as idle, so the command keeps running.

## How to get to it (agent POV)

- REST, global scope: `POST /api/bash/execute_bash_command` (sync),
  `POST /api/bash/start_bash_command` (async),
  `POST /api/bash/bash_commands/{command_id}/stop`,
  `GET /api/bash/bash_events/search`, `GET /api/bash/bash_events/{event_id}`,
  `GET /api/bash/bash_events/` (batch, a GET with a JSON array body) and
  `DELETE /api/bash/bash_events`. Bodies are `ExecuteBashRequest`
  (`command`, optional `cwd`, `timeout` in whole seconds, default 300).
- REST, conversation scope: the same seven routes under
  `/api/conversations/{runtime_conversation_id}/bash/...`, advertised by the
  `conversation_runtime_routes_v1` capability in `/server_info`. They act on
  the conversation's workspace and keep their history in
  `<conversations_path>/<conversation hex>/bash_events`.
- WebSocket: `/sockets/bash-events` (global scope only), first-frame auth,
  `resend_mode=all` replay, client frames start commands.
- Python SDK: `RemoteWorkspace.execute_command()` (start, then polls search
  with `kind__eq=BashOutput`, `command_id__eq` and `order__gt`, joining every
  chunk), `start_command()`, `get_command_output()`, `stop_command()` (treats
  404 as already stopped); with `runtime_conversation_id=...` the same calls
  use the conversation-scoped prefix. `AsyncRemoteWorkspace` mirrors them.
- TypeScript client: `BashClient` (`searchEvents`, `getEvent`, `getEvents`,
  `batchGetEvents`, `startCommand`, `executeCommand`, `clearEvents`,
  `stopCommand`, scoped through `conversationId`), `BashWebSocketClient`
  (query-parameter key, `resendMode: 'all'`), and
  `RemoteWorkspace.executeCommand`, which calls `execute_bash_command` and
  so inherits `F13.execute-large-output`.
- Config: `bash_events_dir` (`OH_BASH_EVENTS_DIR`) and
  `bash_events_retention_seconds` (`OH_BASH_EVENTS_RETENTION_SECONDS`);
  `conversation_idle_ttl_seconds` closes a conversation's scoped service.
- Agent Canvas (context only): terminal tabs start and poll commands through
  these routes.

The recipes drive REST and the WebSocket directly, plus two small SDK
programs (`exec`) for `RemoteWorkspace.execute_command` in both scopes and
one raw `websockets` program that holds the 51 sockets of
`F13.ws-subscriber-cap` in a single process.

## Driving it with control-agent-server

Preconditions:

- A baseline run is live and exported (`launch --new`), `doctor` is ok. No
  LLM is needed: the two conversations below only provide workspaces, are
  never run, and name a model that is never called.
- `jq`, `pgrep` and `sed` are on `PATH` (file counts, the server
  environment, and checks that a killed command's processes are gone).
- `launch` starts the server with cwd `<run>/server`, so global commands
  without a cwd run there.
  ```sh
  NOLLM='{"agent": {"llm": {"model": "openai/qa-f13-never-called", "usage_id": "qa-f13"}, "tools": []}}'
  CID=$(control-agent-server conversation start --body-json "$NOLLM" --no-run --no-autotitle --print-id)
  CID2=$(control-agent-server conversation start --body-json "$NOLLM" --no-run --no-autotitle --print-id)
  WS1=$(control-agent-server api GET "/api/conversations/$CID" --field workspace.working_dir)
  WS2=$(control-agent-server api GET "/api/conversations/$CID2" --field workspace.working_dir)
  SERVER_CWD="$AGENT_SERVER_VERIFY_RUN/server"
  test -d "$WS1" && test -d "$WS2" && test -d "$SERVER_CWD"
  ```

- **Auth (`F13.auth`).** All fourteen REST routes without a key or with a
  wrong one, and a bad socket key followed by a command frame.
  ```sh
  control-agent-server api POST /api/bash/execute_bash_command --auth none \
    --json '{"command": "echo QA_F13_NOAUTH"}' --expect 401 --save F13.auth/execute-no-key
  control-agent-server api POST /api/bash/start_bash_command --auth bad \
    --json '{"command": "echo QA_F13_NOAUTH"}' --expect 401
  control-agent-server api GET /api/bash/bash_events/search --auth bad --expect 401
  control-agent-server api GET /api/bash/bash_events/00000000000000000000000000000000 --auth none --expect 401
  control-agent-server api GET /api/bash/bash_events/ --auth none --json '[]' --expect 401
  control-agent-server api POST /api/bash/bash_commands/00000000000000000000000000000000/stop --auth none --expect 401
  control-agent-server api DELETE /api/bash/bash_events --auth bad --expect 401
  control-agent-server api POST "/api/conversations/$CID/bash/start_bash_command" --auth none \
    --json '{"command": "echo QA_F13_NOAUTH"}' --expect 401
  control-agent-server api POST "/api/conversations/$CID/bash/execute_bash_command" --auth bad \
    --json '{"command": "echo QA_F13_NOAUTH"}' --expect 401
  control-agent-server api GET "/api/conversations/$CID/bash/bash_events/search" --auth none --expect 401
  control-agent-server api GET "/api/conversations/$CID/bash/bash_events/00000000000000000000000000000000" --auth bad --expect 401
  control-agent-server api GET "/api/conversations/$CID/bash/bash_events/" --auth none --json '[]' --expect 401
  control-agent-server api POST "/api/conversations/$CID/bash/bash_commands/00000000000000000000000000000000/stop" --auth none --expect 401
  control-agent-server api DELETE "/api/conversations/$CID/bash/bash_events" --auth none --expect 401
  control-agent-server ws listen /sockets/bash-events --auth bad --send '{"command": "echo QA_F13_NOAUTH"}' \
    --duration 5 --expect-close 4001 --save F13.auth/ws-bad-key
  control-agent-server api GET /api/bash/bash_events/search --query kind__eq=BashCommand \
    --check items not-contains QA_F13_NOAUTH --save F13.auth/nothing-ran
  control-agent-server api GET "/api/conversations/$CID/bash/bash_events/search" --check items len-eq 0
  ```
  Every REST call without a valid key is 401, the socket closes with
  `4001 Authentication failed` before reading the command frame, and with
  the run's key both searches answer 200 with no `QA_F13_NOAUTH` command in
  either scope.
- **Synchronous execute (`F13.execute-sync`).** Exit code, stdout and stderr
  come back in one response.
  ```sh
  EXEC_CMD=$(control-agent-server api POST /api/bash/execute_bash_command \
    --json '{"command": "echo QA_F13_OUT; echo QA_F13_ERR >&2; exit 3", "timeout": 30}' --expect 200 \
    --check kind eq BashOutput --check exit_code eq 3 --check order eq 0 \
    --check stdout eq $'QA_F13_OUT\n' --check stderr eq $'QA_F13_ERR\n' \
    --save F13.execute-sync/execute --field command_id)
  control-agent-server api GET "/api/bash/bash_events/$EXEC_CMD" --check kind eq BashCommand \
    --check command eq 'echo QA_F13_OUT; echo QA_F13_ERR >&2; exit 3' --check timeout eq 30 --check . contains '"cwd": null' \
    --save F13.execute-sync/command-event
  control-agent-server api GET /api/bash/bash_events/search --query command_id__eq="$EXEC_CMD" \
    --check items len-eq 1 --check items.0.exit_code eq 3 --save F13.execute-sync/search
  control-agent-server api POST /api/bash/execute_bash_command --json '{"cwd": "/tmp"}' --expect 422
  control-agent-server api POST /api/bash/execute_bash_command --json '{"command": "true", "timeout": "soon"}' --expect 422
  ```
  The response is the final `BashOutput` (`exit_code` 3, stdout and stderr
  kept apart); the stored `BashCommand` has `"cwd": null` (the key present
  with a null value, not dropped) and `timeout: 30`;
  bodies without `command` or with a text timeout are 422.
- **Global cwd (`F13.global-cwd`).** Default, explicit and missing cwd.
  ```sh
  control-agent-server api POST /api/bash/execute_bash_command --json '{"command": "pwd"}' \
    --check exit_code eq 0 --check stdout eq "$SERVER_CWD"$'\n' --save F13.global-cwd/default
  control-agent-server api POST /api/bash/execute_bash_command --json '{"command": "pwd", "cwd": "/tmp"}' \
    --check exit_code eq 0 --check stdout eq $'/tmp\n'
  control-agent-server api POST /api/bash/execute_bash_command --json '{"command": "pwd", "cwd": "/nonexistent/qa-f13"}' \
    --expect 200 --check exit_code eq -1 --check stderr contains 'Error executing command' --check stdout missing \
    --save F13.global-cwd/missing-cwd
  ```
  `pwd` prints the server's cwd, then `/tmp`; the missing directory is a
  200 whose `BashOutput` has `exit_code` -1 and the spawn error in stderr.
- **Sanitized environment (`F13.env-sanitized`).** The run's server has
  `OH_SESSION_API_KEYS_0` and `OH_SECRET_KEY`; a restart adds the legacy
  `SESSION_API_KEY` (ignored for auth while `OH_SESSION_API_KEYS_0` is set),
  so all three are really in the server's environment.
  ```sh
  control-agent-server restart --env SESSION_API_KEY=qa-f13-legacy-not-a-key
  control-agent-server config | jq -e '.server_env.OH_SESSION_API_KEYS_0 == "<set>" and .server_env.OH_SECRET_KEY == "<set>" and .server_env.SESSION_API_KEY == "<set>"'
  control-agent-server api POST /api/bash/execute_bash_command \
    --json '{"command": "echo keys=${OH_SESSION_API_KEYS_0:-unset} legacy=${SESSION_API_KEY:-unset} secret=${OH_SECRET_KEY:-unset} agent=$AI_AGENT home=$HOME"}' \
    --check exit_code eq 0 \
    --check stdout eq "keys=unset legacy=unset secret=unset agent=openhands home=$AGENT_SERVER_VERIFY_RUN/home"$'\n' \
    --save F13.env-sanitized/env
  ```
  The three credentials are unset inside the command, `AI_AGENT` is
  `openhands`, and the inherited `HOME` (the run's private home) shows the
  rest of the server environment does reach the command.
- **Other credential variables (`F13.env-config-credentials`).** A second
  run whose environment carries a telemetry token and webhook headers
  (aimed at the discard port; it has no conversation, so nothing is posted).
  ```sh
  CRED=$(control-agent-server launch --new --name f13-envcred --env OH_TELEMETRY_HTTP_TOKEN=qa-f13-telemetry-tok \
    --env OH_WEBHOOKS_0_BASE_URL=http://127.0.0.1:9/qa-f13 \
    --env 'OH_WEBHOOKS_0_HEADERS={"Authorization": "Bearer qa-f13-webhook-tok"}' --print-run)
  trap 'control-agent-server stop --run "$CRED" > /dev/null 2>&1 || true' EXIT
  control-agent-server config --run "$CRED" | jq -e '.server_env.OH_SECRET_KEY == "<set>" and .server_env.OH_SESSION_API_KEYS_0 == "<set>"'
  control-agent-server api POST /api/bash/execute_bash_command --run "$CRED" \
    --json '{"command": "env | grep -E \"^(OH_TELEMETRY_HTTP_TOKEN|OH_WEBHOOKS_0_HEADERS|OH_SECRET_KEY|OH_SESSION_API_KEYS_0)=\" | sort"}' \
    --check exit_code eq 0 --check stdout not-contains OH_SECRET_KEY --check stdout not-contains OH_SESSION_API_KEYS_0 \
    --check stdout contains 'OH_TELEMETRY_HTTP_TOKEN=qa-f13-telemetry-tok' \
    --check stdout contains 'OH_WEBHOOKS_0_HEADERS={"Authorization": "Bearer qa-f13-webhook-tok"}' \
    --save F13.env-config-credentials/env
  ```
  The session and cipher keys are in the server's environment and absent
  from the command's, while the telemetry token and the webhook
  `Authorization` header come through verbatim: `sanitized_env`
  (`openhands-sdk/openhands/sdk/utils/command.py`) strips by name only
  `SESSION_API_KEY`, `OH_SECRET_KEY` and the `OH_SESSION_API_KEYS_` prefix,
  the credentials it documents as unlocking user secrets. This records the
  current contract, not an endorsement: a deployment that puts a reusable
  credential (for example the session key) into webhook or refresh headers
  hands it to every bash command and to the agent's terminal. Stripping is
  defense in depth in any case, since commands run as the server's user and
  can read `/proc/<server pid>/environ`.
- **Asynchronous start (`F13.start-async`).** The start returns before the
  output exists; the socket replay waits for the final output.
  ```sh
  START_CMD=$(control-agent-server api POST /api/bash/start_bash_command \
    --json '{"command": "sleep 3; echo QA_F13_ASYNC", "timeout": 30}' --expect 200 --expect-max-ms 2000 \
    --check kind eq BashCommand --check timeout eq 30 --save F13.start-async/start --field id)
  control-agent-server api GET /api/bash/bash_events/search --query command_id__eq="$START_CMD" \
    --check items len-eq 0 --save F13.start-async/before
  control-agent-server ws listen /sockets/bash-events --query resend_mode=all --until-kind BashOutput \
    --until command_id="$START_CMD" --duration 20 --show 0
  control-agent-server api GET /api/bash/bash_events/search --query command_id__eq="$START_CMD" \
    --query kind__eq=BashOutput --check items len-eq 1 --check items.0.exit_code eq 0 \
    --check items.0.stdout eq $'QA_F13_ASYNC\n' --save F13.start-async/after
  ```
  The start answers within two seconds for a three-second command, and right
  after it there is no output; once the `BashOutput` frame for the command
  arrives, search shows one output with `exit_code` 0.
- **Live and replayed socket frames (`F13.ws-live`).** Capture in the
  background, start a command over REST, then replay history on a new
  connection.
  ```sh
  control-agent-server ws start /sockets/bash-events --name f13-live --duration 120 \
    --send '{"command": "echo QA_F13_WS_PROBE"}'
  control-agent-server ws read f13-live --kinds BashOutput --contains QA_F13_WS_PROBE --wait 15 --show 0
  LIVE_CMD=$(control-agent-server api POST /api/bash/start_bash_command \
    --json '{"command": "echo QA_F13_LIVE"}' --field id)
  control-agent-server ws stop f13-live --kinds BashOutput --contains "$LIVE_CMD" --wait 15 --show 0 \
    --save F13.ws-live/live
  control-agent-server ws read f13-live --kinds BashCommand --contains "$LIVE_CMD" --show 0
  control-agent-server ws read f13-live --kinds BashOutput --contains QA_F13_LIVE --expect-min 1 --show 0
  control-agent-server ws listen /sockets/bash-events --query resend_mode=all --until-kind BashOutput \
    --until command_id="$EXEC_CMD" --duration 10 --show 0 --save F13.ws-live/replay
  ```
  The capture's own probe command comes back on it, so it is subscribed
  before the REST start; it then holds the `BashCommand` and the
  `BashOutput` (`QA_F13_LIVE`) of the REST command; the replay connection
  receives the output of the first command of this run, stored before it
  connected.
- **Commands over the socket (`F13.ws-command`).** An invalid frame, then a
  valid one on the same connection.
  ```sh
  control-agent-server ws listen /sockets/bash-events --send '{"cmd": "echo nope"}' \
    --until-kind BashError --until code=ValidationError --duration 10 --save F13.ws-command/error
  control-agent-server ws listen /sockets/bash-events --send '{"cmd": "echo nope"}' \
    --send '{"command": "echo QA_F13_WS_CMD", "timeout": 30}' --expect-kind BashError \
    --until-kind BashOutput --until exit_code=0 --duration 20 --save F13.ws-command/after-error
  control-agent-server api GET /api/bash/bash_events/search --query kind__eq=BashCommand \
    --query sort_order=TIMESTAMP_DESC --query limit=1 --check items.0.command eq 'echo QA_F13_WS_CMD' \
    --check items.0.timeout eq 30
  ```
  The first connection gets `{"kind": "BashError", "code": "ValidationError"}`;
  the second gets the same `BashError` and then, on the same connection, the
  command after the invalid frame still runs (`BashOutput` with `exit_code`
  0); REST search sees it with its `timeout`.
- **Deprecated replay flag (`F13.ws-resend-all`).** `resend_all=true`, then
  a connection with no replay flag that runs a probe.
  ```sh
  NDEP=$(control-agent-server logs --grep 'resend_all is deprecated' | jq .total_matching)
  control-agent-server ws listen /sockets/bash-events --query resend_all=true --until-kind BashOutput \
    --until command_id="$EXEC_CMD" --duration 10 --show 0 --save F13.ws-resend-all/replay
  control-agent-server logs --grep 'resend_all is deprecated' --expect-min $((NDEP + 1))
  control-agent-server ws listen /sockets/bash-events --send '{"command": "echo QA_F13_NO_REPLAY"}' \
    --until-kind BashOutput --until exit_code=0 --expect-frames 2 --duration 15 --save F13.ws-resend-all/no-replay
  ```
  The deprecated flag replays the stored output of this run's first command
  (`EXEC_CMD`) and the server logs `resend_all is deprecated`; without a
  replay flag the connection receives only its probe's `BashCommand` and
  `BashOutput`, two frames, and nothing from history.
- **Subscriber cap (`F13.ws-subscriber-cap`).** One Python program (the
  `websockets` client from the repository's virtualenv, run through `exec`)
  holds 50 authenticated sockets, proves each is subscribed with a probe
  command, then opens a 51st, closes one and opens another. (`ws start`
  costs one CLI process of about 50 MB per socket, so 50 of them would
  hold some 2.5 GB.)
  ```sh
  NCAP=$(control-agent-server logs --grep 'Subscriber limit reached' | jq .total_matching)
  cat > "$AGENT_SERVER_VERIFY_RUN/fixtures/qa_f13_ws_cap.py" <<'PY'
  import json, os, time
  from websockets.exceptions import ConnectionClosed
  from websockets.sync.client import connect
  URL = os.environ["AGENT_SERVER_URL"].replace("http", "ws", 1) + "/sockets/bash-events"
  KEY = os.environ["SESSION_API_KEY"]
  def subscribe():
      ws = connect(URL, open_timeout=10, max_size=None)
      ws.send(json.dumps({"type": "auth", "session_api_key": KEY}))
      return ws
  def probe(ws, marker):
      ws.send(json.dumps({"command": f"echo {marker}"}))
  def wait_output(ws, marker, timeout=20):
      deadline = time.monotonic() + timeout
      while True:
          frame = json.loads(ws.recv(timeout=max(0.1, deadline - time.monotonic())))
          if frame.get("kind") == "BashOutput" and marker in (frame.get("stdout") or ""):
              return
  socks = [subscribe() for _ in range(50)]
  probe(socks[-1], "QA_F13_CAP_FULL")
  for ws in socks:
      wait_output(ws, "QA_F13_CAP_FULL")
  extra = subscribe()
  try:
      extra.recv(timeout=10)
      raise SystemExit("51st socket was neither closed nor silent")
  except ConnectionClosed as exc:
      assert exc.rcvd is not None and exc.rcvd.code == 1013, exc.rcvd
      assert exc.rcvd.reason == "Too many bash event connections", exc.rcvd
  print("QA_F13_CAP_51ST_1013")
  socks.pop().close()
  for attempt in range(20):
      ws = subscribe()
      try:
          probe(ws, f"QA_F13_CAP_SLOT_{attempt}")
          wait_output(ws, f"QA_F13_CAP_SLOT_{attempt}")
          break
      except ConnectionClosed:
          time.sleep(0.5)
  else:
      raise SystemExit("no slot freed after closing one socket")
  socks.append(ws)
  for ws in socks:
      ws.close()
  print("QA_F13_CAP_OK")
  PY
  control-agent-server exec --timeout 120 --expect-output QA_F13_CAP_51ST_1013 --expect-output QA_F13_CAP_OK \
    --save F13.ws-subscriber-cap/program -- .venv/bin/python "$AGENT_SERVER_VERIFY_RUN/fixtures/qa_f13_ws_cap.py"
  control-agent-server logs --grep 'Subscriber limit reached' --expect-min $((NCAP + 1))
  control-agent-server ws listen /sockets/bash-events --send '{"command": "echo QA_F13_CAP_AFTER"}' \
    --until-kind BashOutput --until exit_code=0 --duration 15
  ```
  All 50 sockets receive the probe's output, so all are subscribed; the
  51st is closed with code 1013 and reason `Too many bash event
  connections` (and the server logs `Subscriber limit reached`); after one
  socket closes, a new one subscribes and runs its probe, and once the
  program closes them all, a CLI socket works again. The cap is per server
  for the global scope (`PubSub(max_subscribers=50)` in `bash_service.py`),
  so other consumers' sockets on a shared server count against it.
- **Single and batch reads (`F13.get-events`).** By hex id, then a batch
  with a missing id.
  ```sh
  OUT_ID=$(control-agent-server api GET /api/bash/bash_events/search --query command_id__eq="$EXEC_CMD" \
    --field items.0.id)
  control-agent-server api GET "/api/bash/bash_events/$EXEC_CMD" --check id eq "$EXEC_CMD" \
    --check kind eq BashCommand --save F13.get-events/command
  control-agent-server api GET "/api/bash/bash_events/$OUT_ID" --check kind eq BashOutput \
    --check command_id eq "$EXEC_CMD" --check exit_code eq 3
  control-agent-server api GET /api/bash/bash_events/00000000000000000000000000000000 --expect 404
  control-agent-server api GET /api/bash/bash_events/ \
    --json "[\"$OUT_ID\", \"00000000000000000000000000000000\", \"$EXEC_CMD\"]" --expect 200 \
    --check . len-eq 3 --check 0.kind eq BashOutput --check 1 missing --check 2.id eq "$EXEC_CMD" \
    --save F13.get-events/batch
  control-agent-server api GET /api/bash/bash_events/ --expect 422
  ```
  Both ids resolve, the unknown one is 404, and the batch keeps input order
  with `null` in the middle slot.
- **Search (`F13.search`).** Filters, sort, a UTC time window, paging and
  validation, over three fresh commands.
  ```sh
  T0=$(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)
  for n in 1 2 3; do
    control-agent-server api POST /api/bash/execute_bash_command --json "{\"command\": \"echo QA_F13_PAGE_$n\"}" \
      --check exit_code eq 0 --field command_id
  done
  T1=$(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)
  control-agent-server api GET /api/bash/bash_events/search --query kind__eq=BashCommand \
    --query timestamp__gte="$T0" --query timestamp__lt="$T1" --check items len-eq 3 \
    --check items.0.command eq 'echo QA_F13_PAGE_1' --save F13.search/utc-window
  control-agent-server api GET /api/bash/bash_events/search --query kind__eq=BashCommand \
    --query timestamp__lt="$T0" --check items not-contains QA_F13_PAGE
  control-agent-server api GET /api/bash/bash_events/search --query kind__eq=BashCommand \
    --check items not-contains BashOutput --check items.0.id eq "$EXEC_CMD"
  control-agent-server api GET /api/bash/bash_events/search --query command_id__eq="$EXEC_CMD" \
    --check items len-eq 1 --check items.0.kind eq BashOutput
  NEXT=$(control-agent-server api GET /api/bash/bash_events/search --query kind__eq=BashCommand \
    --query sort_order=TIMESTAMP_DESC --query limit=2 --check items len-eq 2 \
    --check items.0.command eq 'echo QA_F13_PAGE_3' --check items.1.command eq 'echo QA_F13_PAGE_2' \
    --save F13.search/page-1 --field next_page_id)
  control-agent-server api GET /api/bash/bash_events/search --query kind__eq=BashCommand \
    --query sort_order=TIMESTAMP_DESC --query limit=2 --query page_id="$NEXT" \
    --check items.0.command eq 'echo QA_F13_PAGE_1' --save F13.search/page-2
  control-agent-server api GET /api/bash/bash_events/search --query limit=0 --expect 422
  control-agent-server api GET /api/bash/bash_events/search --query limit=101 --expect 422
  control-agent-server api GET /api/bash/bash_events/search --query kind__eq=BashError --expect 422
  control-agent-server api GET /api/bash/bash_events/search --query command_id__eq=not-a-uuid --expect 422
  control-agent-server api GET /api/bash/bash_events/search --query sort_order=NEWEST --expect 422
  ```
  The window holds exactly the three page commands in order; `kind__eq`
  keeps only commands (ascending, the first command of the run first);
  `command_id__eq` returns only that command's outputs; the descending pages
  are PAGE_3, PAGE_2, then PAGE_1; every bad parameter is 422.
- **Offset timestamps (`F13.timestamp-offset`), known bug.** The same instant
  written with `+02:00` must select the same events as its `Z` spelling.
  ```sh
  TZ_CMD=$(control-agent-server api POST /api/bash/execute_bash_command \
    --json '{"command": "echo QA_F13_TZ"}' --field command_id)
  GTE_UTC=$(date -u -d '-1 minute' +%Y-%m-%dT%H:%M:%SZ)
  GTE_PLUS2=$(TZ=Etc/GMT-2 date -d '-1 minute' +%Y-%m-%dT%H:%M:%S%:z)
  control-agent-server api GET /api/bash/bash_events/search --query kind__eq=BashCommand \
    --query timestamp__gte="$GTE_UTC" --check items contains "$TZ_CMD" --save F13.timestamp-offset/utc
  control-agent-server api GET /api/bash/bash_events/search --query kind__eq=BashCommand \
    --query timestamp__gte="$GTE_PLUS2" --check items contains "$TZ_CMD" --save F13.timestamp-offset/plus-two  # bug
  ```
  Expected: both searches contain the command. Today the `Z` window does (the
  positive control) and the `+02:00` window, the `# bug` assertion, is
  empty: the service formats the bound with
  `strftime` without converting it to UTC (`bash_service.py`
  `_timestamp_to_str`), so the bound is read two hours late.
- **Output chunks (`F13.output-chunks`).** 1,100,012 characters of output,
  read back through search and through the SDK.
  ```sh
  BIG_CMD=$(control-agent-server api POST /api/bash/execute_bash_command \
    --json '{"command": "yes a | head -c 1100000; echo QA_F13_TAIL", "timeout": 60}' \
    --expect 200 --check exit_code eq 0 --field command_id)
  control-agent-server api GET /api/bash/bash_events/search --query command_id__eq="$BIG_CMD" \
    --check items len-eq 2 --check items.0.order eq 0 --check items.0.stdout len-eq 1048576 \
    --check items.0.exit_code missing --check items.1.order eq 1 --check items.1.stdout len-eq 51436 \
    --check items.1.exit_code eq 0 --field items.1.order
  control-agent-server api GET /api/bash/bash_events/search --query command_id__eq="$BIG_CMD" \
    --query order__gt=0 --check items len-eq 1 --check items.0.order eq 1 \
    --check items.0.stdout contains QA_F13_TAIL --save F13.output-chunks/order-gt-0 --field items.0.order
  cat > "$AGENT_SERVER_VERIFY_RUN/fixtures/qa_f13_sdk_big.py" <<'PY'
  import os
  from openhands.sdk.workspace import RemoteWorkspace
  ws = RemoteWorkspace(host=os.environ["AGENT_SERVER_URL"], api_key=os.environ["SESSION_API_KEY"], working_dir=os.environ["AGENT_SERVER_VERIFY_RUN"])
  r = ws.execute_command("yes a | head -c 1100000; echo QA_F13_TAIL", timeout=60)
  assert r.exit_code == 0 and len(r.stdout) == 1100012 and r.stdout.endswith("a\nQA_F13_TAIL\n"), (r.exit_code, len(r.stdout))
  print("QA_F13_SDK_BIG_OK", len(r.stdout))
  PY
  control-agent-server exec --expect-output QA_F13_SDK_BIG_OK --save F13.output-chunks/sdk -- \
    .venv/bin/python "$AGENT_SERVER_VERIFY_RUN/fixtures/qa_f13_sdk_big.py"
  ```
  Search returns two chunks (1048576 characters without an exit code, then
  51436 ending in `QA_F13_TAIL` with `exit_code` 0); `order__gt=0` returns
  only the second; the SDK prints `QA_F13_SDK_BIG_OK 1100012`.
- **Order filter and paging (`F13.order-gt-paging`), known bug.** A poller
  that has seen order 0 asks for the next output, one per page.
  ```sh
  control-agent-server api GET /api/bash/bash_events/search --query command_id__eq="$BIG_CMD" \
    --query order__gt=0 --query limit=2 --check items len-eq 1 --check items.0.order eq 1 \
    --check next_page_id missing --quiet --save F13.order-gt-paging/limit-2
  control-agent-server api GET /api/bash/bash_events/search --query command_id__eq="$BIG_CMD" \
    --query order__gt=0 --query limit=1 --check items len-eq 1 --check items.0.order eq 1 \
    --quiet --save F13.order-gt-paging/limit-1  # bug
  ```
  With a page that holds both chunks, `order__gt=0` keeps only chunk 1 (the
  positive control: the filter works). Expected: the one-item page holds
  chunk 1 as well. Today it is empty while `next_page_id`
  is set: `_search_bash_events_sync` (`bash_service.py`) slices the first
  `limit` file names and only then drops outputs with `order <= order__gt`.
  The Python SDK polls with `order__gt` and `limit=100` and ignores
  `next_page_id`, so `RemoteWorkspace.execute_command` never sees chunk 100
  onwards: `yes a | head -c 106000000; echo QA_F13_HUGE_TAIL` finishes on
  the server in about 4 s, yet the SDK (`timeout=40`) returns `exit_code` -1,
  `timeout_occurred` true and exactly 104857600 characters after 40 s. That
  end-to-end repro writes 100 MiB of event files, so it is not a recipe
  here.
- **Large output through execute (`F13.execute-large-output`), known bug.**
  The synchronous route must return everything the command printed.
  ```sh
  control-agent-server api POST /api/bash/execute_bash_command \
    --json '{"command": "yes a | head -c 1100000; echo QA_F13_TAIL", "timeout": 60}' --expect 200 \
    --check exit_code eq 0 --check stdout len-eq 1100012 --quiet --save F13.execute-large-output/execute  # bug
  ```
  Expected: `stdout` holds all 1,100,012 characters (`exit_code` 0 holds
  either way; the length check is the one that fails). Today the response is
  only the last chunk (`order` 1, 51,436 characters): the handler returns
  `page.items[-1]` (`bash_router.py` `execute_bash_command`), so the TS
  client's `RemoteWorkspace.executeCommand` and `BashClient.executeCommand`
  silently lose the first MiB. Past 100 chunks it is worse: `items[-1]` of
  the default 100-item page is chunk 99, so a command that printed 101 MiB
  and ran `exit 7` comes back with `exit_code` null, which the TS
  `RemoteWorkspace.executeCommand` turns into 0.
- **Timeout (`F13.timeout`).** A 30 s sleep with a 2 s timeout. The sleep
  is not the shell's last command, so `/bin/sh` forks it as a child; its
  unique duration makes that child findable with `pgrep`, which proves the
  whole process group is killed, not only the shell. The execute runs in
  the background while `pgrep` sees the child alive.
  ```sh
  SLP=30.$RANDOM
  BODY="{\"command\": \"echo QA_F13_STARTED; sleep $SLP; echo QA_F13_NOT_REACHED\", \"timeout\": 2}"
  ( control-agent-server api POST /api/bash/execute_bash_command --json "$BODY" \
    --expect 200 --timeout 20 --expect-max-ms 8000 --check exit_code eq -1 \
    --check stdout eq $'QA_F13_STARTED\n' --quiet --save F13.timeout/execute ) &
  EXEC_JOB=$!
  for i in $(seq 1 40); do pgrep -fx "sleep $SLP" > /dev/null && break; sleep 0.1; done
  pgrep -fx "sleep $SLP"
  wait "$EXEC_JOB"
  test -z "$(pgrep -fx "sleep $SLP" || true)"
  ```
  The child `sleep` is running during the call; the call returns after about
  two seconds (well under eight) with `exit_code` -1 and the output printed
  before the timeout, and the child is gone.
- **Stop one command (`F13.stop`).** Two running commands, one stopped. As
  in the timeout bullet, the sleep is a forked child with a unique duration.
  ```sh
  SLP=600.$RANDOM
  BODY="{\"command\": \"sleep $SLP; echo QA_F13_NOT_REACHED\"}"
  STOP_CMD=$(control-agent-server api POST /api/bash/start_bash_command --json "$BODY" --field id)
  OTHER_CMD=$(control-agent-server api POST /api/bash/start_bash_command \
    --json '{"command": "sleep 5; echo QA_F13_OTHER"}' --field id)
  for i in $(seq 1 40); do pgrep -fx "sleep $SLP" > /dev/null && break; sleep 0.1; done
  pgrep -fx "sleep $SLP"
  control-agent-server api POST "/api/bash/bash_commands/$STOP_CMD/stop" --expect 200 \
    --check success eq true --save F13.stop/stop
  control-agent-server api GET /api/bash/bash_events/search --query command_id__eq="$OTHER_CMD" \
    --check items len-eq 0
  control-agent-server api GET /api/bash/bash_events/search --query command_id__eq="$STOP_CMD" --until-ok 15 \
    --check items len-eq 1 --check items.0.exit_code eq -15 --check items.0.stdout missing \
    --save F13.stop/stopped-output
  test -z "$(pgrep -fx "sleep $SLP" || true)"
  control-agent-server api GET /api/bash/bash_events/search --query command_id__eq="$OTHER_CMD" --until-ok 15 \
    --check items len-eq 1 --check items.0.exit_code eq 0 --check items.0.stdout eq $'QA_F13_OTHER\n' \
    --save F13.stop/other-output
  ```
  The stopped command's only output has `exit_code` -15 (SIGTERM to its
  process group) and no stdout, and its child `sleep` is gone; the other
  command, still running right after the stop, prints `QA_F13_OTHER` with
  `exit_code` 0.
- **Stop is idempotent (`F13.stop-idempotent`).** Finished, dashed, unknown
  and malformed ids.
  ```sh
  control-agent-server api POST "/api/bash/bash_commands/$STOP_CMD/stop" --expect 200 \
    --check success eq true --save F13.stop-idempotent/again
  STOP_DASHED=$(echo "$STOP_CMD" | sed -E 's/(.{8})(.{4})(.{4})(.{4})(.{12})/\1-\2-\3-\4-\5/')
  control-agent-server api POST "/api/bash/bash_commands/$STOP_DASHED/stop" --expect 200
  control-agent-server api POST "/api/bash/bash_commands/$EXEC_CMD/stop" --expect 200
  control-agent-server api POST /api/bash/bash_commands/00000000000000000000000000000000/stop --expect 404 \
    --save F13.stop-idempotent/unknown
  control-agent-server api POST /api/bash/bash_commands/not-a-uuid/stop --expect 422
  ```
  Finished commands (by hex or dashed id) return `{"success": true}`; an id
  that was never started is 404 (the SDK and TS client treat that as already
  stopped); a non-UUID is 422.
- **Scoped cwd (`F13.scoped-cwd`).** Default, inside, outside, escaping,
  relative, and the SDK's scoped workspace.
  ```sh
  control-agent-server api POST "/api/conversations/$CID/bash/execute_bash_command" \
    --json '{"command": "pwd; mkdir -p qa-sub"}' --expect 200 --check exit_code eq 0 \
    --check stdout eq "$WS1"$'\n' --save F13.scoped-cwd/default
  test -d "$WS1/qa-sub"
  control-agent-server api POST "/api/conversations/$CID/bash/execute_bash_command" \
    --json "{\"command\": \"pwd\", \"cwd\": \"$WS1/qa-sub\"}" --check stdout eq "$WS1/qa-sub"$'\n'
  control-agent-server api POST "/api/conversations/$CID/bash/execute_bash_command" \
    --json "{\"command\": \"pwd\", \"cwd\": \"$WS2\"}" --expect 422 \
    --check detail eq 'cwd must be inside the conversation workspace' --save F13.scoped-cwd/other-workspace
  control-agent-server api POST "/api/conversations/$CID/bash/start_bash_command" \
    --json "{\"command\": \"pwd\", \"cwd\": \"$WS1/qa-sub/../..\"}" --expect 422 \
    --check detail eq 'cwd must be inside the conversation workspace'
  control-agent-server api POST "/api/conversations/$CID/bash/execute_bash_command" \
    --json '{"command": "pwd", "cwd": "qa-sub"}' --expect 422 \
    --check detail eq 'cwd must be inside the conversation workspace'
  cat > "$AGENT_SERVER_VERIFY_RUN/fixtures/qa_f13_sdk_scoped.py" <<'PY'
  import os, sys
  from openhands.sdk.workspace import RemoteWorkspace
  ws = RemoteWorkspace(host=os.environ["AGENT_SERVER_URL"], api_key=os.environ["SESSION_API_KEY"],
                       working_dir=sys.argv[2], runtime_conversation_id=sys.argv[1])
  r = ws.execute_command("pwd", timeout=30)
  assert r.exit_code == 0 and r.stdout == sys.argv[2] + "\n", (r.exit_code, r.stdout, r.stderr)
  print("QA_F13_SDK_SCOPED_OK")
  PY
  control-agent-server exec --expect-output QA_F13_SDK_SCOPED_OK --save F13.scoped-cwd/sdk -- \
    .venv/bin/python "$AGENT_SERVER_VERIFY_RUN/fixtures/qa_f13_sdk_scoped.py" "$CID" "$WS1"
  ```
  `pwd` prints the workspace, then `qa-sub`; another conversation's
  workspace, an escaping path and a relative path are 422 with
  `cwd must be inside the conversation workspace`; the SDK with
  `runtime_conversation_id` lands in the same workspace.
- **Scoped routes (`F13.scoped-routes`).** Execute, read back, start and stop
  inside conversation `CID`.
  ```sh
  SC_EXEC=$(control-agent-server api POST "/api/conversations/$CID/bash/execute_bash_command" \
    --json '{"command": "echo QA_F13_SCOPED > scoped.txt; cat scoped.txt"}' --check exit_code eq 0 \
    --check stdout eq $'QA_F13_SCOPED\n' --save F13.scoped-routes/execute --field command_id)
  test "$(cat "$WS1/scoped.txt")" = QA_F13_SCOPED
  SC_OUT=$(control-agent-server api GET "/api/conversations/$CID/bash/bash_events/search" \
    --query command_id__eq="$SC_EXEC" --check items len-eq 1 --save F13.scoped-routes/search --field items.0.id)
  control-agent-server api GET "/api/conversations/$CID/bash/bash_events/$SC_EXEC" \
    --check kind eq BashCommand --check cwd eq "$WS1" --save F13.scoped-routes/get
  control-agent-server api GET "/api/conversations/$CID/bash/bash_events/" \
    --json "[\"$SC_OUT\", \"00000000000000000000000000000000\"]" --check . len-eq 2 \
    --check 0.stdout eq $'QA_F13_SCOPED\n' --check 1 missing --save F13.scoped-routes/batch
  SLP=600.$RANDOM
  BODY="{\"command\": \"sleep $SLP; echo QA_F13_NOT_REACHED\"}"
  SC_LONG=$(control-agent-server api POST "/api/conversations/$CID/bash/start_bash_command" --json "$BODY" \
    --check cwd eq "$WS1" --field id)
  for i in $(seq 1 40); do pgrep -fx "sleep $SLP" > /dev/null && break; sleep 0.1; done
  pgrep -fx "sleep $SLP"
  control-agent-server api POST "/api/conversations/$CID/bash/bash_commands/$SC_LONG/stop" --expect 200 \
    --check success eq true --save F13.scoped-routes/stop
  control-agent-server api GET "/api/conversations/$CID/bash/bash_events/search" --query command_id__eq="$SC_LONG" \
    --until-ok 15 --check items len-eq 1 --check items.0.exit_code eq -15 --save F13.scoped-routes/stopped
  test -z "$(pgrep -fx "sleep $SLP" || true)"
  ```
  The command wrote `scoped.txt` in the workspace; search, get (with
  `cwd` set to the workspace) and batch read it back; the scoped stop ends
  the sleeping command with `exit_code` -15 and its child `sleep` is gone.
  No socket carries scoped events, so `--until-ok` polls search until the
  final output is written.
- **Scope isolation (`F13.scoped-isolation`).** A scoped command seen from
  every other scope.
  ```sh
  control-agent-server ws start /sockets/bash-events --name f13-iso --duration 120 \
    --send '{"command": "echo QA_F13_ISO_PROBE"}'
  control-agent-server ws read f13-iso --kinds BashOutput --contains QA_F13_ISO_PROBE --wait 15 --show 0
  MARK=QA_F13_ISO_$RANDOM
  ISO_CMD=$(control-agent-server api POST "/api/conversations/$CID/bash/execute_bash_command" \
    --json "{\"command\": \"echo $MARK\"}" --check exit_code eq 0 --field command_id)
  FLUSH_CMD=$(control-agent-server api POST /api/bash/execute_bash_command \
    --json '{"command": "echo QA_F13_FLUSH"}' --field command_id)
  control-agent-server ws stop f13-iso --kinds BashOutput --contains "$FLUSH_CMD" --wait 15 --show 0 \
    --save F13.scoped-isolation/global-socket
  control-agent-server ws read f13-iso --contains "$MARK" --expect-none --show 0
  control-agent-server ws read f13-iso --contains "$ISO_CMD" --expect-none --show 0
  control-agent-server api GET /api/bash/bash_events/search --query kind__eq=BashCommand \
    --query sort_order=TIMESTAMP_DESC --check items not-contains "$MARK" --save F13.scoped-isolation/global-search
  control-agent-server api GET "/api/bash/bash_events/$ISO_CMD" --expect 404
  control-agent-server api POST "/api/bash/bash_commands/$ISO_CMD/stop" --expect 404
  control-agent-server api GET "/api/conversations/$CID2/bash/bash_events/search" --check items len-eq 0 \
    --save F13.scoped-isolation/other-conversation
  control-agent-server api GET "/api/conversations/$CID2/bash/bash_events/$ISO_CMD" --expect 404
  control-agent-server state ls "bash_events/*_BashCommand_$ISO_CMD" --conversation "$CID" --expect-count 1
  control-agent-server state ls "server/workspace/bash_events/*$ISO_CMD*" --expect-count 0
  ```
  The global socket was subscribed (its probe came back) and saw the flush
  command, but no frame with the scoped marker or command id; the global
  search, get and stop and the second conversation do not know the command;
  its event file sits under the conversation's `bash_events` directory and
  not in the global one.
- **Unknown conversation (`F13.scoped-unknown`).** Every scoped route with a
  well-formed id that names no conversation.
  ```sh
  NOCID=00000000-0000-0000-0000-000000000000
  control-agent-server api GET "/api/conversations/$NOCID/bash/bash_events/search" --expect 404 \
    --check detail contains 'Conversation not found' --save F13.scoped-unknown/search
  control-agent-server api GET "/api/conversations/$NOCID/bash/bash_events/00000000000000000000000000000000" --expect 404
  control-agent-server api GET "/api/conversations/$NOCID/bash/bash_events/" --json '["x"]' --expect 404
  control-agent-server api POST "/api/conversations/$NOCID/bash/start_bash_command" \
    --json '{"command": "echo QA_F13_NOCONV"}' --expect 404
  control-agent-server api POST "/api/conversations/$NOCID/bash/execute_bash_command" \
    --json '{"command": "echo QA_F13_NOCONV"}' --expect 404 --save F13.scoped-unknown/execute
  control-agent-server api DELETE "/api/conversations/$NOCID/bash/bash_events" --expect 404
  control-agent-server api POST "/api/conversations/$NOCID/bash/bash_commands/00000000000000000000000000000000/stop" --expect 404
  ```
  All seven are 404 `Conversation not found: 00000000-...`.
- **Restart (`F13.persist-restart`).** History is on disk in both scopes.
  ```sh
  control-agent-server restart
  control-agent-server api GET "/api/bash/bash_events/$EXEC_CMD" --check kind eq BashCommand \
    --save F13.persist-restart/global-get
  control-agent-server api GET /api/bash/bash_events/search --query command_id__eq="$EXEC_CMD" \
    --check items.0.exit_code eq 3 --check items.0.stdout eq $'QA_F13_OUT\n'
  control-agent-server api GET "/api/conversations/$CID/bash/bash_events/search" --query command_id__eq="$SC_EXEC" \
    --check items.0.stdout eq $'QA_F13_SCOPED\n' --save F13.persist-restart/scoped-search
  control-agent-server api POST "/api/bash/bash_commands/$EXEC_CMD/stop" --expect 200
  ```
  After the restart the global command, its output and the scoped output
  are all still readable, and a stop of the finished command still finds it.
- **Clear (`F13.clear`).** Global first, then the conversation's history.
  ```sh
  N_GLOBAL=$(control-agent-server state ls 'server/workspace/bash_events/*' | jq .count)
  test "$N_GLOBAL" -ge 20
  control-agent-server api DELETE /api/bash/bash_events --expect 200 --check cleared_count eq "$N_GLOBAL" \
    --save F13.clear/global
  control-agent-server api GET /api/bash/bash_events/search --check items len-eq 0 --save F13.clear/global-empty
  control-agent-server state ls 'server/workspace/bash_events/*' --expect-count 0
  control-agent-server api POST "/api/bash/bash_commands/$EXEC_CMD/stop" --expect 404
  control-agent-server api GET "/api/conversations/$CID/bash/bash_events/search" --query command_id__eq="$SC_EXEC" \
    --check items len-eq 1
  N_SCOPED=$(control-agent-server state ls 'bash_events/*' --conversation "$CID" | jq .count)
  test "$N_SCOPED" -ge 8
  control-agent-server api DELETE "/api/conversations/$CID/bash/bash_events" --expect 200 \
    --check cleared_count eq "$N_SCOPED" --save F13.clear/scoped
  control-agent-server api GET "/api/conversations/$CID/bash/bash_events/search" --check items len-eq 0
  control-agent-server state ls 'bash_events/*' --conversation "$CID" --expect-count 0
  control-agent-server api DELETE /api/bash/bash_events --check cleared_count eq 0
  ```
  The global clear reports exactly the number of global event files, leaves
  search and the directory empty and makes the finished `EXEC_CMD` unknown
  to stop (404), while the scoped output survives; the scoped clear reports
  and deletes every file in the conversation's directory and leaves nothing
  new in the global store.
- **Retention (`F13.retention`).** The server's own purge window is under
  test, so this bullet sleeps past it.
  ```sh
  RET_CMD=$(control-agent-server api POST /api/bash/execute_bash_command \
    --json '{"command": "echo QA_F13_RETAIN"}' --field command_id)
  SC_RET=$(control-agent-server api POST "/api/conversations/$CID/bash/execute_bash_command" \
    --json '{"command": "echo QA_F13_RETAIN_SCOPED"}' --field command_id)
  sleep 2
  control-agent-server restart --config-json '{"bash_events_retention_seconds": 1}'
  control-agent-server api GET "/api/bash/bash_events/$RET_CMD" --expect 404 --save F13.retention/global-purged
  control-agent-server api GET /api/bash/bash_events/search --check items len-eq 0
  control-agent-server api GET "/api/conversations/$CID/bash/bash_events/$SC_RET" --expect 200 \
    --check command eq 'echo QA_F13_RETAIN_SCOPED' --save F13.retention/scoped-kept
  control-agent-server restart --config-json '{"bash_events_retention_seconds": null}'
  ```
  On startup the purge removes the two-second-old global events; the
  conversation's event is untouched. The last restart switches retention off
  again.
- **Rolling retention (`F13.retention-rolling`).** The same one-second
  window, now for events written after startup. The loop runs at startup
  and then every `max(60, retention / 2)` = 60 s; that timer is under test,
  so the bullet polls for up to 90 s with no restart in between.
  ```sh
  control-agent-server restart --config-json '{"bash_events_retention_seconds": 1}'
  ROLL_CMD=$(control-agent-server api POST /api/bash/execute_bash_command \
    --json '{"command": "echo QA_F13_ROLLING"}' --check exit_code eq 0 --field command_id)
  SC_ROLL=$(control-agent-server api POST "/api/conversations/$CID/bash/execute_bash_command" \
    --json '{"command": "echo QA_F13_ROLLING_SCOPED"}' --check exit_code eq 0 --field command_id)
  UP0=$(control-agent-server api GET /server_info --field uptime)
  control-agent-server api GET "/api/bash/bash_events/$ROLL_CMD" --expect 200 --check command eq 'echo QA_F13_ROLLING' \
    --save F13.retention-rolling/fresh
  T0=$SECONDS
  control-agent-server api GET "/api/bash/bash_events/$ROLL_CMD" --expect 404 --until-ok 90 \
    --save F13.retention-rolling/purged
  test $((SECONDS - T0)) -ge 20
  control-agent-server api GET /server_info --check uptime gt "$UP0"
  control-agent-server api GET /api/bash/bash_events/search --query kind__eq=BashCommand \
    --check items not-contains QA_F13_ROLLING
  control-agent-server api GET "/api/conversations/$CID/bash/bash_events/$SC_ROLL" --expect 200 \
    --check command eq 'echo QA_F13_ROLLING_SCOPED' --save F13.retention-rolling/scoped-kept
  control-agent-server restart --config-json '{"bash_events_retention_seconds": null}'
  ```
  The new global command is readable right after it ran, then answers 404
  once the next pass has run (tens of seconds later, not at once), while
  `/server_info` uptime keeps growing (no restart happened); the scoped
  command written at the same time is still there. The last restart
  switches retention off again.
- **Malformed conversation id (`F13.scoped-bad-id`), known bug.** A non-UUID
  conversation id must be a validation error.
  ```sh
  control-agent-server api GET "/api/conversations/$CID/bash/bash_events/search" --expect 200
  control-agent-server api GET /api/conversations/not-a-uuid/bash/bash_events/search --expect 422 \
    --save F13.scoped-bad-id/search  # bug
  control-agent-server api POST /api/conversations/not-a-uuid/bash/execute_bash_command \
    --json '{"command": "echo QA_F13_BADID"}' --expect 422  # bug
  ```
  The same search on a real conversation id answers 200 (the positive
  control). Expected: 422 for `not-a-uuid`. Today every scoped bash route answers 500
  `'State' object has no attribute 'runtime_event_service'` and logs a
  traceback: FastAPI still calls `get_bash_event_service` after the
  router dependency `bind_local_conversation_runtime` failed validation, and
  it reads `request.state.runtime_event_service`, which was never set.
  Nothing runs. Run `doctor` before this bullet, not after (it fails on the
  logged tracebacks).
- **Idle eviction kills a running command (`F13.scoped-idle-eviction`), known bug.**
  A second run with a 5 s idle TTL and two never-run conversations:
  `EV_CID` with a running scoped command, then `EV_IDLE` with nothing, the
  positive control. The eviction loop's own 60 s timer is under test, so the
  bullet polls (up to 100 s) until the pass has evicted `EV_IDLE`.
  ```sh
  EV=$(control-agent-server launch --new --name f13-evict --config-json '{"conversation_idle_ttl_seconds": 5}' --print-run)
  trap 'control-agent-server stop --run "$EV" > /dev/null 2>&1 || true' EXIT
  EV_CID=$(control-agent-server conversation start --run "$EV" --body-json "$NOLLM" --no-run --no-autotitle --print-id)
  EV_MARK=QA_F13_EVICT_$RANDOM
  EV_CMD=$(control-agent-server api POST "/api/conversations/$EV_CID/bash/start_bash_command" --run "$EV" \
    --json "{\"command\": \"sleep 600 # $EV_MARK\"}" --field id)
  EV_IDLE=$(control-agent-server conversation start --run "$EV" --body-json "$NOLLM" --no-run --no-autotitle --print-id)
  for i in $(seq 1 20); do pgrep -f "$EV_MARK" > /dev/null && break; sleep 0.25; done
  pgrep -f "$EV_MARK"
  control-agent-server state ls owner_lease.json --run "$EV" --conversation "$EV_IDLE" --expect-count 1
  control-agent-server logs --run "$EV" --grep 'Evicted idle' --expect-none
  T0=$SECONDS
  until control-agent-server state ls owner_lease.json --run "$EV" --conversation "$EV_IDLE" --expect-count 0 > /dev/null; do
    test $((SECONDS - T0)) -le 100
    sleep 2
  done
  control-agent-server logs --run "$EV" --grep 'Evicted idle' --expect-min 1
  sleep 3
  pgrep -f "$EV_MARK"  # bug
  control-agent-server api POST "/api/conversations/$EV_CID/bash/bash_commands/$EV_CMD/stop" --run "$EV" --expect 200
  control-agent-server api GET "/api/conversations/$EV_CID/bash/bash_events/search" --run "$EV" \
    --query command_id__eq="$EV_CMD" --until-ok 15 --check items.-1.exit_code eq -15
  ```
  No eviction is logged before the wait; then the idle conversation's lease
  disappears and `Evicted idle conversation` is logged, so an eviction pass
  ran. `EV_CID` was created first, so the same pass reaches it first; the
  3 s settle covers the rest of the pass. Expected: the command is still running after the pass (then
  the bullet stops it and its final output reports -15). Today `EV_CID` is
  evicted in the same pass, its scoped service is closed, the `sleep` is
  SIGKILLed, and no final `BashOutput` is ever written:
  `EventService.is_idle_evictable()` ignores running bash commands.

## Gotchas

- Commands run through `/bin/sh -c` (dash on Debian and Ubuntu), not bash:
  `{1..3}`, `[[ ]]` and `source` fail. No tmux is involved; this family
  needs nothing beyond the baseline.
- Ids in every payload are 32-character hex. `GET .../bash_events/{event_id}`
  matches files by the `_<event_id>` suffix, so a dashed UUID is 404 there,
  while the stop route parses any UUID spelling.
- `command_id__eq` only ever returns `BashOutput` events: `BashCommand`
  file names carry no command id. Fetch the command itself by id.
- The batch read is a GET with a JSON array body and needs the trailing
  slash; browsers' `fetch` cannot send it (the TS client's `batchGetEvents`
  works under Node only).
- An unknown `page_id` silently restarts at the first page, and `order__gt`
  is applied after paging, so a page can hold fewer than `limit` items (even
  none) while `next_page_id` is still set (`F13.order-gt-paging`); follow
  `next_page_id` until it is `null`.
- `timestamp__gte`/`__lt` compare against UTC file-name prefixes: send `Z`
  times (see `F13.timestamp-offset`).
- Exit codes: the real code, `-1` for a timeout or a spawn error (missing
  cwd), `-15` (or `-9` after the 1 s escalation) when stopped. A command
  killed by server shutdown or idle eviction gets no final `BashOutput` at
  all; pollers wait for an exit code that never comes.
- Global cwd is unrestricted and relative to the server process cwd. A
  scoped cwd must be absolute: a relative one is resolved against the server
  process cwd and rejected with 422, even if the directory exists in the
  workspace.
- `/sockets/bash-events` is server-wide: every client sees every global
  command (filter frames by `command_id`), there is no connect-time frame,
  frames keep `null` fields, and at most 50 subscribers are accepted (the
  51st is closed with 1013, `F13.ws-subscriber-cap`). Conversation-scoped commands have no socket;
  poll scoped search (the Python SDK always polls).
- `execute_bash_command` holds the HTTP request open for the whole command;
  give `api --timeout` more than the command's `timeout`.
- `DELETE .../bash_events` does not stop running commands; their later
  outputs are still written.
- Retention (`bash_events_retention_seconds`) applies to the global store
  only, runs at startup and then every `max(60, N/2)` seconds
  (`F13.retention-rolling`), so an event can outlive its window by up to one
  interval.
- In Docker runtime mode (`OH_CONVERSATION_RUNTIME=docker`) the
  `/api/conversations/{id}/...` routes are proxied into the conversation's
  container (`docker_runtime/routers.py`), so scoped cwd values are container
  paths, while `/api/bash` still runs on the outer host. This map covers the
  local runtime only.
- A 5xx body is masked to `{"detail": "Internal Server Error", "exception":
  "..."}`; assert on `exception` for 500.
