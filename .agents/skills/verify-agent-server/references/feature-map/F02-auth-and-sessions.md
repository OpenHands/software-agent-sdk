# Authentication and workspace sessions

How a consumer proves who it is. A server configured with session API keys
accepts exactly one HTTP credential on its `/api/*` routes, the
`X-Session-API-Key` header, and answers 401 to everything else. Its
WebSockets authenticate with a first frame `{"type": "auth",
"session_api_key": ...}` (the query parameter and header forms are deprecated
but still work). Several keys can be valid at once, so keys can be rotated
without downtime, and a server with no keys is open. Browsers cannot attach a
header to `<iframe src>` or `<img src>`, so `POST /api/auth/workspace-session`
exchanges the header for an HttpOnly cookie that only the workspace file
routes honor, and `DELETE` clears it. CORS decides which browser origins may
call the API with credentials.

Source: `openhands-agent-server/openhands/agent_server/auth_router.py`, `openhands-agent-server/openhands/agent_server/dependencies.py`, `openhands-agent-server/openhands/agent_server/middleware.py`, `openhands-agent-server/openhands/agent_server/sockets.py`, `openhands-agent-server/openhands/agent_server/config.py`, `openhands-agent-server/openhands/agent_server/env_parser.py`, `openhands-agent-server/openhands/agent_server/__main__.py`, `openhands-sdk/openhands/sdk/utils/command.py`

Needs: `tmux`

Routes: `POST /api/auth/workspace-session`, `DELETE /api/auth/workspace-session`

## Sub-features

- `F02.http-key-required`: with keys configured, `/api/*` routes answer 401 `{"detail": "Unauthorized"}` to a missing or wrong `X-Session-API-Key` and serve requests that carry the run's key.
- `F02.http-header-only`: regular `/api/*` routes accept the key only in the header: the workspace cookie, the `session_api_key` query parameter and `Authorization: Bearer` all get 401.
- `F02.ws-first-frame`: a first frame `{"type": "auth", "session_api_key": K}` authenticates `/sockets/events/{id}`, `/sockets/session/{id}` and `/sockets/bash-events`.
- `F02.ws-first-frame-reject`: a wrong key, a wrong `type`, a missing key or a non-object first frame closes the socket with 4001 `Authentication failed`, before the conversation is looked up.
- `F02.ws-auth-timeout`: a socket that sends no first frame is closed with 4001 after about 10 seconds.
- `F02.ws-legacy-key`: the deprecated `session_api_key` query parameter and `X-Session-API-Key` header still open a socket, and a redundant auth frame sent after them is ignored.
- `F02.ws-legacy-reject`: a wrong legacy key rejects the handshake itself (HTTP 403, no close frame), even when a correct first frame would follow.
- `F02.workspace-session-mint`: `POST /api/auth/workspace-session` answers 204 with an HttpOnly `oh_workspace_session_key` cookie on `Path=/api/conversations`, `SameSite=none; Secure; Partitioned` on a loopback host, and 401 with no cookie without a valid header (the cookie itself cannot mint another).
- `F02.workspace-cookie-access`: the minted cookie alone (a cookie jar, no header) loads workspace files, also after a restart; a bogus cookie or no credential gets 401.
- `F02.workspace-session-delete`: `DELETE /api/auth/workspace-session` answers 204 with a `Max-Age=0` cookie on the same path, which drops the cookie from the jar so workspace files are 401 again; 401 without a header.
- `F02.multiple-keys`: every configured key (`OH_SESSION_API_KEYS_0`, `_1`, ...) authenticates HTTP and WebSocket clients; an unlisted key does not.
- `F02.rotate-key`: after a restart with a rotated key, the old key gets 401 on HTTP and 4001 on WebSockets and its workspace cookie stops working, while the new key works and conversations persist.
- `F02.keys-hidden-from-commands`: commands the server runs do not see `OH_SESSION_API_KEYS_*`, `SESSION_API_KEY` or `OH_SECRET_KEY` in their environment.
- `F02.cors-default-origins`: CORS preflights from `localhost` and `127.0.0.1` origins (any port) are allowed with credentials; other origins and `Origin: null` get 400 `Disallowed CORS origin`, and simple responses carry no `Access-Control-Allow-Origin` for them.
- `F02.cors-configured-origins`: `OH_ALLOW_CORS_ORIGINS_*`, `OH_ALLOW_CORS_ORIGIN_REGEX` and `DOCKER_HOST_ADDR` extend the allowed origins after a restart.
- `F02.cors-workspace-routes`: the workspace-session and workspace file routes allow any http(s) origin with credentials but refuse `Origin: null`, and that wildcard does not reach other routes.
- `F02.no-auth-mode`: a server with no keys serves `/api/*` without a header (ignoring any header), accepts sockets without an auth frame, and mints an empty workspace cookie.
- `F02.legacy-env-key`: the legacy `SESSION_API_KEY` variable alone turns authentication on with that key.
- `F02.default-bind`: a server started without `--host` listens on `127.0.0.1` when no session key is configured and on `0.0.0.0` when one is; `--host 0.0.0.0` without a key still binds but logs the unauthenticated-bind warning.
- `F02.workspace-cookie-root-path`: behind a path prefix (`OH_WEB_URL` with a path), a cookie minted through the prefixed URL is sent back by the browser to the prefixed workspace file URLs.
- `F02.workspace-session-ipv6-loopback`: a mint reached through the IPv6 loopback (`Host: [::1]:PORT`) gets `Secure; Partitioned`, like `localhost` and `127.0.0.1`.

## How to get to it (agent POV)

- REST: every `/api/*` route takes `X-Session-API-Key` when keys are
  configured. The probes and `/server_info` (F01) and `GET /api/init` need no
  key; the OpenAI-compatible `/v1/*` gateway also accepts
  `Authorization: Bearer` (its own family). An unknown `/api` path is 404 even
  without a key.
- REST: `POST /api/auth/workspace-session` and
  `DELETE /api/auth/workspace-session` (both need the header, no body, 204).
  The cookie is honored only by `GET /api/conversations/{conversation_id}/workspace`
  and `GET /api/conversations/{conversation_id}/workspace/{file_path:path}`
  (the workspace file routes, owned by another family; used here as the
  second view).
- WebSocket: `/sockets/events/{conversation_id}`,
  `/sockets/session/{conversation_id}` and `/sockets/bash-events` share one
  auth helper (`_accept_authenticated_websocket` in `sockets.py`): first frame,
  or the deprecated `?session_api_key=` / `X-Session-API-Key` header.
- SDK: `RemoteWorkspace(host=..., api_key=...)` sends the header on every
  request (`openhands-sdk/openhands/sdk/workspace/remote/remote_workspace_mixin.py`);
  `RemoteConversation`'s event socket sends the first auth frame
  (`remote_conversation.py`, `_client_loop`).
- TypeScript client: `HttpClient` sends `X-Session-API-Key` from `apiKey`;
  `RemoteWorkspace.startWorkspaceSession(conversationId)` and
  `deleteWorkspaceSession()` call the two routes with
  `credentials: 'include'` and return the workspace base URL;
  `ConversationEventStream` authenticates with the first frame, while
  `BashWebSocketClient` still uses the deprecated query parameter.
- Configuration: `OH_SESSION_API_KEYS_0..N` (or `session_api_keys` in the
  config file), legacy `SESSION_API_KEY`; CORS: `OH_ALLOW_CORS_ORIGINS_0..N`,
  `OH_ALLOW_CORS_ORIGIN_REGEX`, `DOCKER_HOST_ADDR`; `OH_WEB_URL` with a path
  sets the app's root path.
- Command line: `python -m openhands.agent_server [--host H] [--port 8000]`
  (`__main__.py`). Without `--host` the bind address follows the auth
  posture: loopback without keys, all interfaces with keys.
- Agent Canvas mints the cookie before embedding workspace HTML, images and
  PDFs in iframes from another origin (context only).
- Recipes: `api --auth none|bad|previous` and `--header` drive the HTTP
  cases; `ws listen --auth first-frame|query|header|none|bad` the socket
  cases; a small cookie-jar helper (`curl` through `control-agent-server exec`,
  written in `Preconditions:`) plays the browser for cookies and CORS
  preflights; `restart --env`, `restart --rotate-key` and second servers
  (`launch --new --no-auth`) reach the other modes; `bind.sh` (also in
  `Preconditions:`) starts the server without the launcher to see its default
  bind address.

## Driving it with control-agent-server

Preconditions:

- A baseline run is live and exported (`launch --new`), `doctor` is ok. No
  LLM profile is needed. `curl` and `jq` are on `PATH`; `tmux` is needed for
  the bash-events frames.
- The block below creates a workspace with one HTML file, a conversation on
  it that never runs (`CID`), and `jar.sh`: one browser-like `curl` request
  that keeps cookies in `<run>/fixtures/qa-f02-cookies.txt`, sends the run's
  key only when told `key`, prints headers, body and status, and fails unless
  the status matches and every `has:TEXT` / `lacks:TEXT` (case-insensitive)
  holds. Run it through `control-agent-server exec`, which injects
  `AGENT_SERVER_URL` and `SESSION_API_KEY` for the chosen run.
- It also writes `bind.sh` for `F02.default-bind`: started inside a private
  network namespace (`unshare -rn`, so nothing outside it can connect, and
  port 8000 is free in every replay), it runs this checkout's server the way
  an operator would (`python -m openhands.agent_server`, no `--host` unless
  given, default port 8000) with its own `HOME` and config, waits until
  `/proc/net/tcp` shows the listening socket, stops the server, prints its
  log and `listening on ADDRESS:8000`, and fails if nothing listened.
  Needs `unshare` (util-linux) with unprivileged user namespaces, or root.
- Several bullets restart this run (`restart --env`, `--rotate-key`); the
  environment they add stays for later bullets. Bullets that need another
  server mode launch a second run and stop it when the bullet ends.

  ```sh
  F02="$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f02"
  mkdir -p "$F02/workspace"
  printf '<p>qa-f02-workspace-file</p>\n' > "$F02/workspace/qa-f02.html"
  cat > "$F02/jar.sh" <<'EOF'
  # jar.sh METHOD PATH key|nokey STATUS [has:TEXT|lacks:TEXT]... [-- CURL_ARGS...]
  method=$1 path=$2 auth=$3 want=$4
  shift 4
  checks=()
  while [ $# -gt 0 ] && [ "$1" != "--" ]; do checks+=("$1"); shift; done
  [ "${1:-}" = "--" ] && shift
  if [ "$auth" = key ]; then set -- "$@" -H "X-Session-API-Key: $SESSION_API_KEY"; fi
  jar="$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f02-cookies.txt"
  out=$(curl -sS --noproxy '*' -D - -b "$jar" -c "$jar" -X "$method" -w '\nstatus=%{http_code}\n' "$@" "$AGENT_SERVER_URL$path")
  printf '%s\n' "$out"
  printf '%s\n' "$out" | grep -qx "status=$want" || { echo "FAIL: status is not $want" >&2; exit 1; }
  for c in "${checks[@]}"; do
    case $c in
      has:*) printf '%s\n' "$out" | grep -qiF -- "${c#has:}" || { echo "FAIL: missing ${c#has:}" >&2; exit 1; } ;;
      lacks:*) if printf '%s\n' "$out" | grep -qiF -- "${c#lacks:}"; then echo "FAIL: unexpected ${c#lacks:}" >&2; exit 1; fi ;;
    esac
  done
  EOF
  cat > "$F02/bind.sh" <<'EOF'
  # bind.sh DIR [SERVER_ARGS...], inside `unshare -rn env -i PATH=... QA_F02_PYTHON=...`
  dir=$1; shift
  mkdir -p "$dir/home"
  cd "$dir" || exit 1
  printf '{"preload_tools": false, "enable_vscode": false}\n' > config.json
  HOME="$dir/home" OH_PERSISTENCE_DIR="$dir/home/.openhands" OPENHANDS_AGENT_SERVER_CONFIG_PATH="$dir/config.json" \
    COLUMNS=400 PYTHONUNBUFFERED=1 "$QA_F02_PYTHON" -m openhands.agent_server "$@" > server.log 2>&1 &
  pid=$!
  addr=
  for _ in $(seq 240); do
    addr=$(awk '$4 == "0A" && $2 ~ /:1F40$/ {print $2}' /proc/net/tcp)
    [ -n "$addr" ] && break
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.5
  done
  kill "$pid" 2>/dev/null
  wait "$pid"
  cat server.log
  case $addr in
    00000000:1F40) echo 'listening on 0.0.0.0:8000' ;;
    0100007F:1F40) echo 'listening on 127.0.0.1:8000' ;;
    *) echo "FAIL: no listening socket on port 8000 (${addr:-none})" >&2; exit 1 ;;
  esac
  EOF
  CID=$(control-agent-server conversation start --no-run --tools none --no-autotitle --workspace "$F02/workspace" --print-id)
  control-agent-server api GET "/api/conversations/$CID/workspace/qa-f02.html" --expect 200
  ```

- **Key required on HTTP (`F02.http-key-required`).** Call a route without a
  key, with a wrong key, and with the run's key.
  ```sh
  control-agent-server api GET /api/conversations/count --auth none --expect 401 --check detail eq Unauthorized --save F02.http-key-required/no-key
  control-agent-server api GET /api/conversations/count --auth bad --expect 401 --check detail eq Unauthorized --save F02.http-key-required/wrong-key
  control-agent-server api GET "/api/conversations/$CID" --auth none --expect 401
  control-agent-server api GET /api/conversations/count --expect 200 --check . ge 1 --save F02.http-key-required/run-key
  ```
  Both bad calls are 401 `{"detail": "Unauthorized"}`; with the key the count
  is at least 1 (the conversation from `Preconditions:`).
- **Header only (`F02.http-header-only`).** Offer the right key everywhere
  except the header.
  ```sh
  KEY=$(cat "$AGENT_SERVER_VERIFY_RUN/private/session_api_key")
  control-agent-server api GET "/api/conversations/$CID" --auth none --cookie "oh_workspace_session_key=$KEY" --expect 401 --save F02.http-header-only/cookie
  control-agent-server api GET /api/conversations/count --auth none --query "session_api_key=$KEY" --expect 401 --save F02.http-header-only/query
  control-agent-server api GET /api/conversations/count --auth none --header "Authorization: Bearer $KEY" --expect 401 --save F02.http-header-only/bearer
  control-agent-server api GET "/api/conversations/$CID" --expect 200 --check id eq "$CID"
  unset KEY
  ```
  All three are 401. The cookie case matters because the browser sends the
  cookie to every path under `/api/conversations`, including this one.
- **First-frame auth on every socket (`F02.ws-first-frame`).** The CLI's
  default `--auth first-frame` sends `{"type": "auth", "session_api_key": K}`
  first; there is no acknowledgement, so each socket proves it by what it
  sends next.
  ```sh
  control-agent-server ws listen "/sockets/events/$CID" --until-kind ConversationStateUpdateEvent --duration 15 --save F02.ws-first-frame/events
  control-agent-server ws listen "/sockets/session/$CID" --query after_seq=-1 --until-kind sync --duration 15 --save F02.ws-first-frame/session
  control-agent-server ws listen /sockets/bash-events --send '{"command":"echo qa-f02-first-frame"}' --until-kind BashOutput --duration 20 --save F02.ws-first-frame/bash
  ```
  The events socket sends a full-state `ConversationStateUpdateEvent`, the
  session socket a `sync` frame, and the bash socket runs the command
  (`BashCommand`, then `BashOutput`).
- **Rejected first frames (`F02.ws-first-frame-reject`).** Wrong key, wrong
  type, missing key, not an object, and a wrong key for a conversation that
  does not exist.
  ```sh
  control-agent-server ws listen "/sockets/events/$CID" --auth bad --expect-close 4001 --duration 10 --save F02.ws-first-frame-reject/events-wrong-key \
    | jq -e '.close.reason == "Authentication failed"'
  control-agent-server ws listen "/sockets/session/$CID" --auth bad --expect-close 4001 --duration 10
  control-agent-server ws listen /sockets/bash-events --auth bad --send '{"command":"echo qa-f02-refused"}' --expect-close 4001 --expect-no-kind BashOutput --duration 10 --save F02.ws-first-frame-reject/bash-wrong-key
  control-agent-server ws listen /sockets/bash-events --auth none --send '{"type":"hello","session_api_key":"x"}' --expect-close 4001 --duration 10 --save F02.ws-first-frame-reject/wrong-type
  control-agent-server ws listen /sockets/bash-events --auth none --send '{"type":"auth"}' --expect-close 4001 --duration 10
  control-agent-server ws listen /sockets/bash-events --auth none --send '[1]' --expect-close 4001 --duration 10
  control-agent-server ws listen /sockets/events/00000000-0000-4000-8000-000000000000 --auth bad --expect-close 4001 --duration 10 --save F02.ws-first-frame-reject/unknown-unauthenticated
  control-agent-server ws listen /sockets/events/00000000-0000-4000-8000-000000000000 --expect-close 4004 --duration 10
  ```
  Every rejection is a close frame with code 4001 and reason
  `Authentication failed`, and a command sent right after a wrong key never
  runs (no `BashOutput`). An unauthenticated socket to an unknown
  conversation also gets 4001; only an authenticated one learns 4004
  `Conversation not found`.
- **Auth timeout (`F02.ws-auth-timeout`).** Connect and say nothing. The
  server's own 10-second timer is under test, so the bullet measures it.
  ```sh
  T0=$SECONDS
  control-agent-server ws listen /sockets/bash-events --auth none --expect-close 4001 --duration 25 --save F02.ws-auth-timeout/silent
  test $((SECONDS - T0)) -ge 9
  test $((SECONDS - T0)) -le 16
  ```
  The close arrives with 4001 after about 10 seconds, not at once and not
  at the end of the 25-second capture.
- **Deprecated query and header keys (`F02.ws-legacy-key`).** Authenticate
  with `?session_api_key=` or the header and send a command as the very
  first frame, then send a redundant auth frame with a wrong key before a
  command.
  ```sh
  control-agent-server ws listen /sockets/bash-events --auth query --send '{"command":"echo qa-f02-query"}' --until-kind BashOutput --duration 20 --save F02.ws-legacy-key/query
  control-agent-server ws listen /sockets/bash-events --auth header --send '{"command":"echo qa-f02-header"}' --until-kind BashOutput --duration 20 --save F02.ws-legacy-key/header
  control-agent-server ws listen /sockets/bash-events --auth query --send '{"type":"auth","session_api_key":"qa-f02-ignored"}' --send '{"command":"echo qa-f02-legacy"}' --until-kind BashOutput --duration 20 --save F02.ws-legacy-key/redundant-frame
  control-agent-server ws listen "/sockets/events/$CID" --auth header --until-kind ConversationStateUpdateEvent --duration 15
  ```
  Both legacy forms authenticate at the handshake: a command sent as the
  first frame runs (were the legacy key ignored, that frame would be read as
  a failed auth frame and closed with 4001). The redundant auth frame is
  skipped (its key is never checked) and the command still runs. The server
  logs `session_api_key passed via query param or header is deprecated`
  (wrapped over several log lines). A socket that merely stays open for a
  few seconds proves nothing: an unauthenticated one waits up to 10 seconds
  for its auth frame.
- **Wrong legacy key fails the handshake (`F02.ws-legacy-reject`).** A wrong
  query key, and a wrong query key followed by a correct first frame.
  ```sh
  control-agent-server ws listen /sockets/bash-events --auth none --query session_api_key=qa-f02-wrong --expect-reject 403 --duration 5 --save F02.ws-legacy-reject/wrong-query
  control-agent-server ws listen /sockets/bash-events --auth bad-header --expect-reject 403 --duration 5 --save F02.ws-legacy-reject/wrong-header
  control-agent-server ws listen /sockets/bash-events --query session_api_key=qa-f02-stale --send '{"command":"echo qa-f02-stale"}' \
    --expect-reject 403 --expect-frames 0 --duration 5 --save F02.ws-legacy-reject/stale-query-good-frame
  ```
  The handshake is refused with HTTP 403 (the server closes with 4001 before
  accepting, which the client sees as a rejected upgrade), so there is no
  4001 close frame, and a present legacy key is never overridden by a
  correct first frame.
- **Mint the workspace cookie (`F02.workspace-session-mint`).** Mint with
  the key, without it, with a wrong one, with only the cookie, and with an
  ignored body.
  ```sh
  control-agent-server api POST /api/auth/workspace-session --expect 204 --save F02.workspace-session-mint/loopback \
    | jq -e '.response.headers["set-cookie"] | startswith("oh_workspace_session_key=") and contains("HttpOnly") and contains("Max-Age=315360000") and contains("Path=/api/conversations") and contains("SameSite=none") and contains("Secure") and contains("Partitioned")'
  control-agent-server api POST /api/auth/workspace-session --auth none --expect 401 --check detail eq Unauthorized --save F02.workspace-session-mint/no-key \
    | jq -e '.response.headers["set-cookie"] == null'
  control-agent-server api POST /api/auth/workspace-session --auth bad --expect 401 --check detail eq Unauthorized | jq -e '.response.headers["set-cookie"] == null'
  KEY=$(cat "$AGENT_SERVER_VERIFY_RUN/private/session_api_key")
  control-agent-server api POST /api/auth/workspace-session --auth none --cookie "oh_workspace_session_key=$KEY" --expect 401 --save F02.workspace-session-mint/cookie-only
  unset KEY
  control-agent-server api POST /api/auth/workspace-session --json '{"ignored": true}' --expect 204
  ```
  The cookie value is the session key itself (the CLI prints it redacted);
  the 401 responses set no cookie. The route sits in the header-only group,
  so a cross-site request that carries only the cookie cannot mint or renew
  it.
- **The cookie alone opens workspace files (`F02.workspace-cookie-access`).**
  The browser flow: mint into the jar, then load the file with no header,
  also after a restart.
  ```sh
  control-agent-server exec --save F02.workspace-cookie-access/mint -- bash "$F02/jar.sh" POST /api/auth/workspace-session key 204 'has:set-cookie: oh_workspace_session_key='
  grep -q oh_workspace_session_key "$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f02-cookies.txt"
  control-agent-server exec --save F02.workspace-cookie-access/file-with-cookie -- bash "$F02/jar.sh" GET "/api/conversations/$CID/workspace/qa-f02.html" nokey 200 'has:qa-f02-workspace-file'
  control-agent-server restart
  control-agent-server exec --save F02.workspace-cookie-access/after-restart -- bash "$F02/jar.sh" GET "/api/conversations/$CID/workspace/qa-f02.html" nokey 200 'has:qa-f02-workspace-file'
  control-agent-server api GET "/api/conversations/$CID/workspace/qa-f02.html" --auth none --cookie oh_workspace_session_key=qa-f02-bogus --expect 401 --save F02.workspace-cookie-access/bogus-cookie
  control-agent-server api GET "/api/conversations/$CID/workspace/qa-f02.html" --auth none --expect 401
  control-agent-server api GET "/api/conversations/$CID/workspace/qa-f02.html" --expect 200
  ```
  The file loads with only the cookie, before and after the restart (the
  cookie is the key, so there is no server-side session to lose). A bogus
  cookie or none is 401; the header alone also works.
- **Delete the workspace cookie (`F02.workspace-session-delete`).** Clear it
  through the API, then through the jar.
  ```sh
  control-agent-server api DELETE /api/auth/workspace-session --expect 204 --save F02.workspace-session-delete/delete \
    | jq -e '.response.headers["set-cookie"] | startswith("oh_workspace_session_key=\"\"") and contains("Max-Age=0") and contains("Path=/api/conversations") and contains("HttpOnly")'
  control-agent-server api DELETE /api/auth/workspace-session --auth none --expect 401 --check detail eq Unauthorized
  control-agent-server exec --save F02.workspace-session-delete/jar-delete -- bash "$F02/jar.sh" DELETE /api/auth/workspace-session key 204 'has:max-age=0'
  if grep oh_workspace_session_key "$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f02-cookies.txt"; then false; fi
  control-agent-server exec --save F02.workspace-session-delete/file-after-delete -- bash "$F02/jar.sh" GET "/api/conversations/$CID/workspace/qa-f02.html" nokey 401
  ```
  The clearing cookie has an empty value, `Max-Age=0` and the same path, so
  the jar drops it and the next file request is 401. Deleting still needs
  the header.
- **A second key (`F02.multiple-keys`).** Add `OH_SESSION_API_KEYS_1` with a
  restart and use both keys.
  ```sh
  control-agent-server restart --env OH_SESSION_API_KEYS_1=qa-f02-second-key
  control-agent-server api GET /api/conversations/count --auth none --header 'X-Session-API-Key: qa-f02-second-key' --expect 200 --save F02.multiple-keys/second-key
  control-agent-server api GET /api/conversations/count --expect 200
  control-agent-server api GET /api/conversations/count --auth none --header 'X-Session-API-Key: qa-f02-third-key' --expect 401
  control-agent-server ws listen /sockets/bash-events --auth none --send '{"type":"auth","session_api_key":"qa-f02-second-key"}' --send '{"command":"echo qa-f02-second"}' --until-kind BashOutput --duration 20 --save F02.multiple-keys/ws-second-key
  control-agent-server doctor
  ```
  Both keys work on HTTP and on the socket; an unlisted key is 401.
- **Rotate the key (`F02.rotate-key`).** Mint a cookie with the current key,
  restart with a new one, and try the old key everywhere.
  ```sh
  control-agent-server exec -- bash "$F02/jar.sh" POST /api/auth/workspace-session key 204
  control-agent-server exec -- bash "$F02/jar.sh" GET "/api/conversations/$CID/workspace/qa-f02.html" nokey 200 'has:qa-f02-workspace-file'
  control-agent-server restart --rotate-key
  OLD_KEY=$(cat "$AGENT_SERVER_VERIFY_RUN/private/previous_session_api_key")
  control-agent-server api GET /api/conversations/count --auth previous --expect 401 --save F02.rotate-key/old-key
  control-agent-server ws listen /sockets/bash-events --auth none --send '{"type":"auth","session_api_key":"'"$OLD_KEY"'"}' --expect-close 4001 --duration 10 --save F02.rotate-key/ws-old-key
  unset OLD_KEY
  control-agent-server exec --save F02.rotate-key/old-cookie -- bash "$F02/jar.sh" GET "/api/conversations/$CID/workspace/qa-f02.html" nokey 401
  control-agent-server api GET "/api/conversations/$CID" --expect 200 --check id eq "$CID" --save F02.rotate-key/new-key
  control-agent-server ws listen /sockets/bash-events --send '{"command":"echo qa-f02-rotated"}' --until-kind BashOutput --duration 20
  control-agent-server doctor
  ```
  The old key is 401 on HTTP and 4001 on the socket, the cookie minted with
  it (which opened the file just before the restart) no longer opens files,
  and the new key reads the same conversation.
  `doctor` confirms the rotated-out key is rejected.
- **Keys stay out of commands (`F02.keys-hidden-from-commands`).** Run `env`
  through the server (the global bash route) with two indexed keys and a
  secret key configured.
  ```sh
  KEY=$(cat "$AGENT_SERVER_VERIFY_RUN/private/session_api_key")
  SECRET=$(cat "$AGENT_SERVER_VERIFY_RUN/private/secret_key")
  control-agent-server api POST /api/bash/execute_bash_command --json '{"command":"env"}' --expect 200 \
    --check exit_code eq 0 --check stdout contains PATH= \
    --check stdout not-contains OH_SESSION_API_KEYS --check stdout not-contains SESSION_API_KEY \
    --check stdout not-contains OH_SECRET_KEY --check stdout not-contains qa-f02-second-key \
    --check stdout not-contains "$KEY" --check stdout not-contains "$SECRET" --save F02.keys-hidden-from-commands/env
  unset KEY SECRET
  ```
  The command's environment has none of the key variables or values (the
  run's current key, the second key from `F02.multiple-keys` and the secret
  key are all set on the server process at this point).
- **Default CORS origins (`F02.cors-default-origins`).** Preflights from
  local origins, a foreign origin and `null`, then a simple request from the
  foreign origin.
  ```sh
  control-agent-server exec --save F02.cors-default-origins/localhost -- bash "$F02/jar.sh" OPTIONS /api/conversations/count nokey 200 \
    'has:access-control-allow-origin: http://localhost:3001' 'has:access-control-allow-credentials: true' -- \
    -H 'Origin: http://localhost:3001' -H 'Access-Control-Request-Method: GET' -H 'Access-Control-Request-Headers: x-session-api-key'
  control-agent-server exec -- bash "$F02/jar.sh" OPTIONS /api/settings nokey 200 'has:access-control-allow-origin: http://127.0.0.1:5173' -- \
    -H 'Origin: http://127.0.0.1:5173' -H 'Access-Control-Request-Method: PATCH'
  control-agent-server exec --save F02.cors-default-origins/foreign -- bash "$F02/jar.sh" OPTIONS /api/conversations/count nokey 400 \
    'has:Disallowed CORS origin' 'lacks:access-control-allow-origin' -- -H 'Origin: https://qa-evil.example' -H 'Access-Control-Request-Method: GET'
  control-agent-server exec -- bash "$F02/jar.sh" OPTIONS /api/conversations/count nokey 400 'has:Disallowed CORS origin' -- \
    -H 'Origin: null' -H 'Access-Control-Request-Method: GET'
  control-agent-server exec --save F02.cors-default-origins/simple-foreign -- bash "$F02/jar.sh" GET /api/conversations/count key 200 \
    'lacks:access-control-allow-origin' -- -H 'Origin: https://qa-evil.example'
  ```
  Local origins are echoed with `access-control-allow-credentials: true`; a
  foreign origin and `null` get 400 `Disallowed CORS origin`, and the simple
  GET from the foreign origin succeeds for a non-browser client but carries
  no `access-control-allow-origin`, so a browser would hide it.
- **Configured CORS origins (`F02.cors-configured-origins`).** Restart with
  one exact origin, one regex and a Docker host address.
  ```sh
  control-agent-server restart --env OH_ALLOW_CORS_ORIGINS_0=https://qa-canvas.example \
    --env 'OH_ALLOW_CORS_ORIGIN_REGEX=https://[a-z0-9-]+\.qa-regex\.example' --env DOCKER_HOST_ADDR=10.254.0.7
  control-agent-server exec --save F02.cors-configured-origins/exact -- bash "$F02/jar.sh" OPTIONS /api/conversations/count nokey 200 \
    'has:access-control-allow-origin: https://qa-canvas.example' -- -H 'Origin: https://qa-canvas.example' -H 'Access-Control-Request-Method: GET'
  control-agent-server exec --save F02.cors-configured-origins/regex -- bash "$F02/jar.sh" OPTIONS /api/conversations/count nokey 200 \
    'has:access-control-allow-origin: https://app-1.qa-regex.example' -- -H 'Origin: https://app-1.qa-regex.example' -H 'Access-Control-Request-Method: GET'
  control-agent-server exec --save F02.cors-configured-origins/docker-host -- bash "$F02/jar.sh" OPTIONS /api/conversations/count nokey 200 \
    'has:access-control-allow-origin: http://10.254.0.7:3000' -- -H 'Origin: http://10.254.0.7:3000' -H 'Access-Control-Request-Method: GET'
  control-agent-server exec -- bash "$F02/jar.sh" OPTIONS /api/conversations/count nokey 400 'has:Disallowed CORS origin' -- \
    -H 'Origin: https://qa-other.example' -H 'Access-Control-Request-Method: GET'
  ```
  The three configured origins are echoed; an unrelated origin is still 400.
- **Workspace routes accept any origin (`F02.cors-workspace-routes`).** The
  cookie routes echo any http(s) origin, but not `null`, and only on those
  paths.
  ```sh
  control-agent-server exec --save F02.cors-workspace-routes/session -- bash "$F02/jar.sh" OPTIONS /api/auth/workspace-session nokey 200 \
    'has:access-control-allow-origin: https://qa-anywhere.example' 'has:access-control-allow-credentials: true' -- \
    -H 'Origin: https://qa-anywhere.example' -H 'Access-Control-Request-Method: POST' -H 'Access-Control-Request-Headers: x-session-api-key'
  control-agent-server exec --save F02.cors-workspace-routes/file -- bash "$F02/jar.sh" OPTIONS "/api/conversations/$CID/workspace/qa-f02.html" nokey 200 \
    'has:access-control-allow-origin: https://qa-anywhere.example' -- -H 'Origin: https://qa-anywhere.example' -H 'Access-Control-Request-Method: GET'
  control-agent-server exec --save F02.cors-workspace-routes/null-origin -- bash "$F02/jar.sh" OPTIONS /api/auth/workspace-session nokey 400 'has:Disallowed CORS origin' -- \
    -H 'Origin: null' -H 'Access-Control-Request-Method: POST'
  control-agent-server exec --save F02.cors-workspace-routes/not-elsewhere -- bash "$F02/jar.sh" OPTIONS "/api/conversations/$CID" nokey 400 'has:Disallowed CORS origin' -- \
    -H 'Origin: https://qa-anywhere.example' -H 'Access-Control-Request-Method: GET'
  ```
  Both cookie routes echo the origin with credentials; `null` is refused,
  and the same origin is refused on the conversation itself.
- **Unauthenticated mode (`F02.no-auth-mode`).** A second server launched
  with `--no-auth`.
  ```sh
  NOAUTH=$(control-agent-server launch --new --no-auth --name f02-noauth --print-run)
  trap 'control-agent-server stop --run "$NOAUTH" >/dev/null' EXIT
  control-agent-server api GET /api/conversations/count --run "$NOAUTH" --auth none --expect 200 --save F02.no-auth-mode/no-key
  control-agent-server api GET /api/conversations/count --run "$NOAUTH" --auth none --header 'X-Session-API-Key: qa-f02-anything' --expect 200
  control-agent-server ws listen /sockets/bash-events --run "$NOAUTH" --auth none --send '{"command":"echo qa-f02-open"}' --until-kind BashOutput --duration 20 --save F02.no-auth-mode/ws
  control-agent-server api POST /api/auth/workspace-session --run "$NOAUTH" --auth none --expect 204 --save F02.no-auth-mode/mint \
    | jq -e '.response.headers["set-cookie"] | startswith("oh_workspace_session_key=\"\"")'
  control-agent-server api DELETE /api/auth/workspace-session --run "$NOAUTH" --auth none --expect 204
  control-agent-server stop --run "$NOAUTH"
  ```
  Everything answers without a key, any header is ignored, the socket runs
  a command sent as its very first frame, and the minted cookie is empty.
  The second run's evidence lands under its own run directory.
- **Legacy `SESSION_API_KEY` (`F02.legacy-env-key`).** A second server whose
  only key is the legacy variable.
  ```sh
  LEGACY=$(control-agent-server launch --new --no-auth --name f02-legacy --env SESSION_API_KEY=qa-f02-legacy-key --print-run)
  trap 'control-agent-server stop --run "$LEGACY" >/dev/null' EXIT
  control-agent-server api GET /api/conversations/count --run "$LEGACY" --auth none --expect 401 --save F02.legacy-env-key/no-key
  control-agent-server api GET /api/conversations/count --run "$LEGACY" --auth none --header 'X-Session-API-Key: qa-f02-legacy-key' --expect 200 --save F02.legacy-env-key/legacy-key
  control-agent-server ws listen /sockets/bash-events --run "$LEGACY" --auth none --send '{"type":"auth","session_api_key":"qa-f02-legacy-key"}' --send '{"command":"echo qa-f02-legacy-ws"}' --until-kind BashOutput --duration 20
  control-agent-server api POST /api/bash/execute_bash_command --run "$LEGACY" --auth none --header 'X-Session-API-Key: qa-f02-legacy-key' \
    --json '{"command":"env"}' --expect 200 --check exit_code eq 0 --check stdout contains PATH= \
    --check stdout not-contains qa-f02-legacy-key --check stdout not-contains SESSION_API_KEY --save F02.legacy-env-key/env
  control-agent-server stop --run "$LEGACY"
  ```
  The legacy key turns authentication on (401 without it, 200 with it, and
  the socket accepts it); `env` runs (exit 0, `PATH=` in its output) and
  shows neither the variable nor its value.
- **Default bind address (`F02.default-bind`).** Start this checkout's
  server three times without the launcher, as an operator would, each in its
  own private network namespace: no key and no `--host`, a key and no
  `--host`, and no key with an explicit `--host 0.0.0.0`.
  ```sh
  QA_F02_PYTHON="$(jq -r .checkout "$AGENT_SERVER_VERIFY_RUN/run.json")/.venv/bin/python"
  control-agent-server exec --save F02.default-bind/no-key --expect-output 'Starting OpenHands Agent Server on 127.0.0.1:8000' \
    --expect-output 'listening on 127.0.0.1:8000' --reject-output 'Binding to all interfaces' -- \
    unshare -rn env -i PATH="$PATH" QA_F02_PYTHON="$QA_F02_PYTHON" bash "$F02/bind.sh" "$F02/bind-no-key"
  control-agent-server exec --save F02.default-bind/with-key --expect-output 'Starting OpenHands Agent Server on 0.0.0.0:8000' \
    --expect-output 'listening on 0.0.0.0:8000' --reject-output 'Binding to all interfaces' -- \
    unshare -rn env -i PATH="$PATH" QA_F02_PYTHON="$QA_F02_PYTHON" OH_SESSION_API_KEYS_0=qa-f02-bind-key bash "$F02/bind.sh" "$F02/bind-key"
  control-agent-server exec --save F02.default-bind/wildcard-no-key --expect-output 'listening on 0.0.0.0:8000' \
    --expect-output 'Binding to all interfaces (0.0.0.0) without a session API key' -- \
    unshare -rn env -i PATH="$PATH" QA_F02_PYTHON="$QA_F02_PYTHON" bash "$F02/bind.sh" "$F02/bind-wildcard" --host 0.0.0.0
  ```
  Without keys the server listens on `127.0.0.1:8000` only; with
  `OH_SESSION_API_KEYS_0` it listens on every interface (`0.0.0.0`), and
  neither logs the warning. An explicit `--host 0.0.0.0` without a key is
  honored (it listens on `0.0.0.0`) and logs `Binding to all interfaces
  (0.0.0.0) without a session API key`. The address comes from the kernel's
  socket table (`/proc/net/tcp`), not only from the startup line. The
  namespace keeps the unauthenticated wildcard server unreachable from
  outside, and `env -i` keeps this shell's `OH_*` variables away from it.
- **Cookie path ignores a prefix (`F02.workspace-cookie-root-path`), known bug.**
  A second server with `OH_WEB_URL=http://127.0.0.1/qa-f02-prefix`, used
  through its prefixed URLs like a browser behind a path-prefix proxy.
  ```sh
  PREFIXED=$(control-agent-server launch --new --name f02-prefix --env OH_WEB_URL=http://127.0.0.1/qa-f02-prefix --print-run)
  trap 'control-agent-server stop --run "$PREFIXED" >/dev/null' EXIT
  PCID=$(control-agent-server conversation start --run "$PREFIXED" --no-run --tools none --no-autotitle --workspace "$F02/workspace" --print-id)
  control-agent-server api GET /openapi.json --run "$PREFIXED" --auth none --max-chars 200 --check servers.0.url eq /qa-f02-prefix
  control-agent-server exec --run "$PREFIXED" -- bash "$F02/jar.sh" GET "/qa-f02-prefix/api/conversations/$PCID/workspace/qa-f02.html" key 200 'has:qa-f02-workspace-file'
  control-agent-server exec --run "$PREFIXED" --save F02.workspace-cookie-root-path/mint -- bash "$F02/jar.sh" POST /qa-f02-prefix/api/auth/workspace-session key 204
  grep -q oh_workspace_session_key "$PREFIXED/fixtures/qa-f02-cookies.txt"
  control-agent-server exec --run "$PREFIXED" -- bash "$F02/jar.sh" GET "/api/conversations/$PCID/workspace/qa-f02.html" nokey 200 'has:qa-f02-workspace-file'
  control-agent-server exec --run "$PREFIXED" --save F02.workspace-cookie-root-path/file-with-cookie -- bash "$F02/jar.sh" GET "/qa-f02-prefix/api/conversations/$PCID/workspace/qa-f02.html" nokey 200 'has:qa-f02-workspace-file'  # bug
  control-agent-server stop --run "$PREFIXED"
  ```
  The server advertises `servers: [{"url": "/qa-f02-prefix"}]` and serves
  the file under the prefix; the minted cookie lands in the second run's jar
  and is a valid credential (the unprefixed URL, which a prefix-stripping
  proxy would forward, loads the file with it). Expected: the cookie also
  comes back on the prefixed file URL, the one the browser actually uses.
  Today it is scoped to `Path=/api/conversations` regardless of the root
  path (unlike `CORSDispatcher`, which strips `root_path`), so the jar does
  not send it and the file is 401. The TypeScript client's
  `startWorkspaceSession()` returns `${host}/api/conversations/{id}/workspace/`,
  which carries the prefix whenever `host` does.
- **IPv6 loopback is not a secure context (`F02.workspace-session-ipv6-loopback`), known bug.**
  Mint as browsers at the IPv4 loopback names would, then as a browser at
  `http://[::1]:PORT` would.
  ```sh
  control-agent-server api POST /api/auth/workspace-session --header 'Host: 127.0.0.1:8000' --expect 204 \
    --check-header set-cookie contains SameSite=none --check-header set-cookie contains Secure --check-header set-cookie contains Partitioned
  control-agent-server api POST /api/auth/workspace-session --header 'Host: localhost:8000' --expect 204 \
    --check-header set-cookie contains Secure --check-header set-cookie contains Partitioned
  control-agent-server api POST /api/auth/workspace-session --header 'Host: [::1]:8000' --expect 204 --save F02.workspace-session-ipv6-loopback/ipv6-host \
    --check-header set-cookie contains SameSite=none --check-header set-cookie contains Secure --check-header set-cookie contains Partitioned  # bug
  ```
  `127.0.0.1` and `localhost` get `Secure; Partitioned`. Expected: the
  `Host: [::1]:8000` mint gets them too, since `::1` is in the server's
  loopback list. Today it gets `SameSite=none` without `Secure`, which
  browsers reject, so workspace embeds fail for a client at
  `http://[::1]:PORT`: `_request_is_secure_context` handles brackets only in
  `X-Forwarded-Host`, and splits the unbracketed `request.url.hostname`
  (`::1`) on `:`, leaving an empty host.

## Gotchas

- The cookie value is the session key itself (HttpOnly, ten-year
  `Max-Age`). There is no server-side session: `DELETE` only tells the
  browser to drop it, a copied cookie keeps working until the key is rotated,
  and rotating the key invalidates every cookie at once.
- `Path=/api/conversations` makes browsers send the cookie to every
  conversation route, not just workspace files; the server ignores it there
  (`F02.http-header-only`). Keep it that way: honoring it would open those
  routes to CSRF.
- A wrong legacy key (query or header) is rejected before the WebSocket is
  accepted, so the client sees an HTTP 403 handshake failure, not a 4001
  close. `ws listen` records it as `close.http_status`; assert it with
  `--expect-reject 403` (`--expect-close 4001` fails on it).
- First-frame auth has no acknowledgement frame and a 10-second deadline: a
  client that connects and then waits for something else is closed with 4001.
  Prove a socket is authenticated by a frame it sends afterwards.
- The bash socket treats any later `{"type": "auth", ...}` frame as a no-op,
  whatever its key; other frames must be `ExecuteBashRequest` JSON.
- CORS preflights are answered by middleware before routing and auth, so an
  `OPTIONS` request never gets 401. Without `Origin` and
  `Access-Control-Request-Method` it reaches routing and gets 405.
  The `curl` helper plays an independent browser for these and for cookies
  (path matching, `Max-Age=0` removal); `api OPTIONS`, `--check-header` and
  `--jar` reach the same cases inside the CLI.
- Stripping the key variables from command environments is defense in depth:
  commands run as the server's user and can read `/proc/<server pid>/environ`.
- `restart --env` values persist for later restarts of the same run; this
  family leaves `OH_SESSION_API_KEYS_1` and the CORS variables on its run.
- With keys configured and no `--host`, the server binds `0.0.0.0`; without
  keys, `127.0.0.1` (`F02.default-bind`). The launcher always passes
  `--host 127.0.0.1`, so only `F02.default-bind` sees the default.
