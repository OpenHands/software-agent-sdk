# Server status, info and contract

Probes and metadata every consumer reads before anything else: liveness,
health and readiness for orchestrators, `/server_info` (and its `/` alias) for
versions, build identity, usable tools, capability flags and idle time, and the
OpenAPI document that clients generate types from. All of them answer without
a session key, even when authentication is on. Three server-wide settings
change what these surfaces show: `static_files_path` turns `/` into a redirect
to an unauthenticated `/static` mount, `preload_tools` runs a tool preload
before `/ready` flips, and `DEBUG` adds a stack trace to 5xx bodies.

Source: `openhands-agent-server/openhands/agent_server/server_details_router.py`, `openhands-agent-server/openhands/agent_server/api.py`, `openhands-agent-server/openhands/agent_server/openapi.py`, `openhands-agent-server/openhands/agent_server/tool_preload_service.py`, `openhands-agent-server/openhands/agent_server/config.py`

Routes: `GET /alive`, `GET /health`, `GET /ready`, `GET /server_info`, `GET /`,
`GET /openapi.json`

## Sub-features

- `F01.alive`: `GET /alive` returns 200 `{"status": "ok"}` without a session key, while a protected route refuses the same key-less request.
- `F01.health`: `GET /health` returns 200 `{"status": "ok"}` without a session key.
- `F01.ready`: `GET /ready` returns 200 `{"status": "ready"}` once startup finished, without a session key.
- `F01.server-info`: `GET /server_info` reports package versions, `build_git_sha` equal to the served checkout, usable tools and capability flags, without a session key.
- `F01.root-info`: without static files, `GET /` returns the same server info document as `/server_info` (every field but the clocks).
- `F01.uptime-restart`: `uptime` grows while the process lives and starts again from zero after a restart.
- `F01.idle-time`: `idle_time` grows while nothing happens and resets when a file operation runs.
- `F01.openapi`: `GET /openapi.json` serves the OpenAPI document with the conversation routes and without the WebSockets.
- `F01.static-files`: with `static_files_path` set to an existing directory, `GET /` answers 302 to `/static/index.html` (or `/static/` without an `index.html`), `/static/*` serves the directory without a session key and refuses to escape it, `/api/*` still needs the key and `/server_info` is unchanged; a missing directory keeps `/` as server info.
- `F01.debug-tracebacks`: with `DEBUG=true` a 5xx body carries a `traceback` next to `detail` and `exception`; without it, or on a 4xx, it does not.
- `F01.preload-tools`: with `preload_tools` on, startup runs the tool preload before `/ready` answers 200; when the server finds no Chromium the preload fails, is logged and skipped and the server still serves, and with a Chromium it can find the preload succeeds.

## How to get to it (agent POV)

- REST: `GET /alive`, `GET /health`, `GET /ready` (Kubernetes-style probes; the
  launcher and `doctor` use them), `GET /server_info` and `GET /`.
- REST: `GET /openapi.json`; `/docs` and `/redoc` render it for humans (not
  mapped).
- Static mount: `GET /static/{path}`, present only when `static_files_path`
  (env `OH_STATIC_FILES_PATH`) names an existing directory; it is a Starlette
  mount, so `map routes` does not list it. `scripts/agent_server_ui/run.sh`
  uses it to serve a bundled UI.
- Config: `preload_tools` (env `OH_PRELOAD_TOOLS`, default `true` in
  production; `launch` writes `false` unless `--preload-tools`), and the
  process environment variable `DEBUG` (read once at import by
  `openhands.sdk.logger`; the Docker runtime forwards it into containers).
- SDK: `RemoteWorkspace` health checks and the Docker/API workspaces poll
  `/alive` and `/ready` before using a server; Agent Canvas reads
  `/server_info` to gate features on `capabilities` and versions.
- TypeScript client: the generated schema in `clients/typescript/src/generated/`
  is built from the OpenAPI document of the pinned server release.

## Driving it with control-agent-server

Preconditions:

- A baseline run is live and exported (`launch --new`), `doctor` is ok. No
  LLM profile is needed.
- Commands run from the repository root, so `git rev-parse HEAD` names the
  served checkout. `jq` is installed.
- The static-files, DEBUG and preload bullets restart the run with
  `--config-json`/`--env` and end with `restart --reset-config`, so they come
  last. The block below creates the run-owned fixture directory every bullet
  writes into (never a shared `/tmp` path).
  ```sh
  F01="$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f01"
  mkdir -p "$F01"
  ```

- **Liveness (`F01.alive`).** Probe without a key; a protected route refuses
  the same key-less request, so authentication is on.
  ```sh
  control-agent-server api GET /api/conversations/count --auth none --expect 401
  control-agent-server api GET /alive --auth none --expect 200 --check status eq ok --save F01.alive/alive
  ```
  The protected route is `401`; `/alive` is `200` with `{"status": "ok"}`
  although no key was sent.
- **Health (`F01.health`).** Probe without a key.
  ```sh
  control-agent-server api GET /health --auth none --expect 200 --check status eq ok --save F01.health/health
  ```
  The status is `200` and the body is `{"status": "ok"}`.
- **Readiness (`F01.ready`).** Probe without a key after startup.
  ```sh
  control-agent-server api GET /ready --auth none --expect 200 --check status eq ready --save F01.ready/ready
  ```
  The body is `{"status": "ready"}`.
- **Server info (`F01.server-info`).** Read build identity and capabilities
  without a key.
  ```sh
  control-agent-server api GET /server_info --auth none --expect 200 \
    --check build_git_sha eq "$(git rev-parse HEAD)" \
    --check capabilities contains idempotent_conversation_create_v1 \
    --check capabilities contains tool_catalog_v1 \
    --check usable_tools contains terminal \
    --check conversation_runtime eq local \
    --check sdk_version exists --save F01.server-info/info
  ```
  Every check passes; the saved body also has `version`, `tools_version` and
  `workspace_version`.
- **Root alias (`F01.root-info`).** `/` serves the same document.
  ```sh
  control-agent-server api GET /server_info --auth none --expect 200 --quiet --raw-out "$F01/server_info.json"
  control-agent-server api GET / --auth none --expect 200 --check build_git_sha eq "$(git rev-parse HEAD)" \
    --raw-out "$F01/root.json" --save F01.root-info/root
  diff <(jq -S 'del(.uptime, .idle_time)' "$F01/server_info.json") <(jq -S 'del(.uptime, .idle_time)' "$F01/root.json")
  ```
  `diff` prints nothing: both bodies are identical apart from the two clocks.
- **Uptime across a restart (`F01.uptime-restart`).** The server's own clock
  is under test, so this bullet sleeps.
  ```sh
  U0=$(control-agent-server api GET /server_info --auth none --field uptime)
  sleep 3
  U1=$(control-agent-server api GET /server_info --auth none --check uptime gt "$U0" --check uptime ge 3 --field uptime)
  T0=$SECONDS
  control-agent-server restart
  control-agent-server api GET /server_info --auth none --check uptime lt "$U1" --check uptime le "$((SECONDS - T0))" \
    --save F01.uptime-restart/after-restart
  control-agent-server doctor
  ```
  `uptime` grew across the sleep and `U1` is at least `3.0`; after the restart
  `uptime` is no larger than the seconds since the restart began (an
  unrestarted clock would be at least `U1` larger), and `doctor` is ok again.
- **Idle time (`F01.idle-time`).** Status probes are not activity; a file
  operation is.
  ```sh
  rm -rf "$F01/idle"
  sleep 3
  I1=$(control-agent-server api GET /server_info --auth none --field idle_time)
  control-agent-server api GET /server_info --auth none --check idle_time ge 3
  test ! -e "$F01/idle"
  control-agent-server api POST /api/file/create_directory --query "path=$F01/idle" --expect 200
  test -d "$F01/idle"
  control-agent-server api GET /server_info --auth none --check idle_time lt "$I1" --save F01.idle-time/after-mkdir
  ```
  `idle_time` is at least `3.0` although probes ran in between, the directory
  did not exist before and exists on disk after the call, and `idle_time`
  drops after it is created.
- **OpenAPI document (`F01.openapi`).** The contract clients generate from.
  ```sh
  control-agent-server api GET /openapi.json --auth none --expect 200 --max-chars 200 \
    --check 'paths./api/conversations.post.operationId' matches start_conversation \
    --check 'paths./api/conversations/{conversation_id}/pause' exists \
    --check 'paths./sockets/events/{conversation_id}' missing --save F01.openapi/openapi
  ```
  All checks pass. WebSockets are not part of the OpenAPI document;
  `control-agent-server map routes --module sockets` lists them.
- **Static files (`F01.static-files`).** Restart with `static_files_path` on
  a run-owned directory, read it without a key, try to escape it, drop
  `index.html`, then point the setting at a missing directory and restore.
  ```sh
  S="$F01/static"
  mkdir -p "$S"
  printf '<!doctype html><title>qa-f01</title>QA_F01_STATIC\n' > "$S/index.html"
  printf '{"qa": "f01-static"}\n' > "$S/qa.json"
  printf '{"qa": "f01-outside"}\n' > "$F01/server_info"
  M=/static  # a mount, not a route: map check cannot match its paths, so they go through a variable
  control-agent-server restart --config-json "{\"static_files_path\": \"$S\"}"
  control-agent-server api GET / --auth none --expect 302 --check-header location eq /static/index.html \
    --save F01.static-files/root-redirect
  control-agent-server api GET "$M/index.html" --auth none --expect 200 --check-header content-type contains text/html \
    --raw-out "$F01/index.got"
  cmp "$S/index.html" "$F01/index.got"
  control-agent-server api GET "$M/qa.json" --auth none --expect 200 --check qa eq f01-static --save F01.static-files/file
  control-agent-server api GET "$M/%2e%2e/server_info" --auth none --expect 404 --save F01.static-files/escape
  control-agent-server api GET /api/conversations/count --auth none --expect 401
  control-agent-server api GET /api/conversations/count --expect 200
  control-agent-server api GET /server_info --auth none --expect 200 --check build_git_sha eq "$(git rev-parse HEAD)" \
    --check capabilities contains tool_catalog_v1
  mv "$S/index.html" "$S/index.moved"
  control-agent-server api GET / --auth none --expect 302 --check-header location eq /static/ --save F01.static-files/no-index
  control-agent-server api GET "$M/" --auth none --expect 404
  mv "$S/index.moved" "$S/index.html"
  control-agent-server restart --config-json "{\"static_files_path\": \"$F01/static-missing\"}"
  control-agent-server api GET / --auth none --expect 200 --check build_git_sha eq "$(git rev-parse HEAD)" \
    --save F01.static-files/missing-dir
  control-agent-server api GET "$M/qa.json" --auth none --expect 404
  control-agent-server restart --reset-config
  control-agent-server api GET / --auth none --expect 200 --check title eq 'OpenHands Agent Server'
  ```
  `/` is a 302 to `/static/index.html`, and both static files are served
  byte for byte without a key. The escape attempt is 404: the server decodes
  it to `/static/../server_info`, which the mount refuses (it would serve
  `qa-f01/server_info` if it followed `..`, and a client or router that
  normalized the path would reach the real `/server_info`, 200 either way).
  `/api/*` still refuses a key-less call and accepts the run's key, and
  `/server_info` is unchanged. Without `index.html` the redirect targets
  `/static/`, which is 404 (the mount does not render directory indexes).
  A `static_files_path` that does not exist keeps `/` as server info and
  mounts nothing.
- **DEBUG tracebacks (`F01.debug-tracebacks`).** A deliberate 5xx (the
  workspaces registry file replaced by a directory, as in `F17.io-error`)
  read with and without `DEBUG`.
  ```sh
  WS="$AGENT_SERVER_VERIFY_RUN/home/.openhands/workspaces.json"
  test ! -e "$WS"
  mkdir -p "$WS"
  control-agent-server api GET /api/workspaces --expect 500 --check exception contains 'Failed to read workspaces' \
    --check traceback missing --save F01.debug-tracebacks/off
  N=$(control-agent-server logs --grep 'DEBUG mode: ENABLED' | jq .total_matching)
  control-agent-server restart --env DEBUG=true
  control-agent-server logs --grep 'DEBUG mode: ENABLED' --expect-min $((N + 1))
  control-agent-server api GET /api/workspaces --expect 500 --check detail eq 'Internal Server Error' \
    --check exception contains 'Failed to read workspaces' \
    --check traceback contains 'HTTPException: 500: Failed to read workspaces' --save F01.debug-tracebacks/on
  control-agent-server api GET /api/conversations/count --auth none --expect 401 --check traceback missing
  control-agent-server restart --reset-config
  control-agent-server api GET /api/workspaces --expect 500 --check traceback missing
  rmdir "$WS"
  control-agent-server api GET /api/workspaces --expect 200 --check workspaces len-eq 0
  ```
  Without `DEBUG` the 500 body is `detail`, `exception` and nothing else; with
  `DEBUG=true` the server prints `DEBUG mode: ENABLED` and the same 500 adds a
  `traceback` string whose last line is the `HTTPException`, while the 401
  stays `{"detail": ...}`. After `--reset-config` the trace is gone again and
  the registry reads normally once the directory is removed.
- **Tool preload (`F01.preload-tools`).** Restart with `preload_tools` on,
  first with whatever Chromium the server finds on its own, then with the
  machine's Chromium linked into the run's Playwright cache (when the machine
  has one), then restore.
  ```sh
  control-agent-server logs --grep 'Tool preload service is disabled' --expect-min 1
  STARTED=$(control-agent-server logs --grep 'Tool preload service started' | jq .total_matching)
  FAILED=$(control-agent-server logs --grep 'Tool preload service failed to start' | jq .total_matching)
  control-agent-server restart --config-json '{"preload_tools": true}'
  control-agent-server config | jq -e '.config_file.preload_tools == true'
  control-agent-server api GET /ready --auth none --expect 200 --check status eq ready --save F01.preload-tools/ready
  control-agent-server api GET /api/conversations/count --expect 200
  if control-agent-server api GET /server_info --auth none --check usable_tools not-contains browser_tool_set >/dev/null; then
    control-agent-server logs --grep 'Tool preload service failed to start' --expect-min $((FAILED + 1))
    control-agent-server logs --grep 'Error preloading' --expect-min 1
    test "$(control-agent-server logs --grep 'Tool preload service started' | jq .total_matching)" -eq "$STARTED"
  else
    control-agent-server logs --grep 'Tool preload service started' --expect-min $((STARTED + 1))
  fi
  if [ "$(control-agent-server capabilities | jq -r '.machine.chromium.available')" = true ]; then
    CHROME=$(control-agent-server capabilities | jq -r '.machine.chromium.detail')
    PW="$AGENT_SERVER_VERIFY_RUN/home/.cache/ms-playwright/chromium-qa-f01"
    mkdir -p "$PW/chrome-linux"
    ln -sfn "$(readlink -f "$CHROME")" "$PW/chrome-linux/chrome"
    test -x "$PW/chrome-linux/chrome"
    STARTED=$(control-agent-server logs --grep 'Tool preload service started' | jq .total_matching)
    control-agent-server restart
    control-agent-server logs --grep 'Tool preload service started' --expect-min $((STARTED + 1))
    control-agent-server logs --grep 'Chromium is available' --expect-min 1
    control-agent-server api GET /ready --auth none --expect 200 --check status eq ready --save F01.preload-tools/ready-chromium
    rm -r "$PW"
  fi
  control-agent-server restart --reset-config
  control-agent-server config | jq -e '.config_file.preload_tools == false'
  ```
  Before the bullet the log says the preload is disabled; the counts taken
  then make each later log check about the restart that follows them. With
  `preload_tools` on, the restart (which waits for `/ready`) returns, `/ready`
  is 200 and `/api/*` serves. A server that finds no Chromium (no
  `browser_tool_set` in `usable_tools`; on this machine the only Chromium is
  under `$PLAYWRIGHT_BROWSERS_PATH`, which the server ignores) logs `Error
  preloading chromium` with the install hint and `Tool preload service failed
  to start - skipping`, and still serves. With the machine's Chromium linked
  into the run's `~/.cache/ms-playwright`, the next start logs `Chromium is
  available for browser operations` and one more `Tool preload service
  started successfully`. The last restart writes `preload_tools: false` again.

## Gotchas

- `/ready` returns 503 `{"status": "initializing"}` until startup finished (tool
  preload, VS Code); `launch` waits for it, so a 503 on a launched run means
  something is wrong. In deferred-init mode `/ready` stays 503 until
  `POST /api/init` (see the deferred init family).
- `idle_time` resets on conversation events (except state updates), bash
  command start, execute and stop, file uploads, downloads, directory creation
  and archives, and the git changes and diff routes; not on status probes or
  other reads. Orchestrators use it to reap idle sandboxes.
- `build_git_sha` and `build_git_ref` come from `OPENHANDS_BUILD_GIT_SHA` and
  `OPENHANDS_BUILD_GIT_REF`; `launch` sets them from the checkout. A server
  started another way reports `unknown`, which is why `doctor` compares the
  SHA only for launched runs.
- `uptime` and `idle_time` are whole seconds serialized as floats (`12.0`);
  compare them with `--check ... lt`, not with the shell's integer `test`.
- Dotted `--check` and `--field` paths may contain slashes and braces
  (`paths./api/conversations.post`), because only dots separate segments.
- `static_files_path` is checked once at startup: a directory created later
  needs a restart, while `index.html` is looked up on every `GET /`. The
  `/static` mount sits outside every auth group, so anything in that
  directory is public.
- The preload only checks for Chromium and builds the browser executor and
  tool action types; it starts no browser process (the browser starts on the
  first browser action). A failed preload never blocks startup, but an
  exception escaping the preload task would (`api.py` re-raises it). The
  server log is Rich-wrapped at 80 columns, so grep for short phrases
  (`Tool preload service started`, not the whole sentence).
- `DEBUG` is read once at import and also turns the log level to `DEBUG`; it
  leaks file paths and source lines in 5xx bodies, so it is never for
  production. Unhandled exceptions get the same `traceback` field next to
  their `error_id`; the map's unhandled 500s either need a conversation or
  are known bugs, so this family drives the deliberate `HTTPException` 500,
  the cheapest stable 5xx.
