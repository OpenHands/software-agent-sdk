# Python SDK remote client (RemoteConversation, RemoteWorkspace)

The consumer lane: what a Python program built on this checkout's SDK
observes when it drives the Agent Server. `RemoteWorkspace` (and
`AsyncRemoteWorkspace`) runs commands, moves files, reads git state and
fetches the server's LLM profiles and secret references over REST;
`Conversation(agent, workspace=RemoteWorkspace(...))` returns a
`RemoteConversation` that creates or reattaches a server conversation, sends
messages, runs it to completion while streaming events to callbacks over the
events WebSocket, and steers it (pause, interrupt, confirmation, secrets, ask,
title, fork, close). The same program carries server-side hooks, client-defined
tools, custom tool modules, token streaming and completion logs. Every recipe
runs a real SDK program through
`control-agent-server exec` and checks the server's own view through the REST
API afterwards, so an SDK that reports success while the server disagrees
fails here.

Source: `openhands-sdk/openhands/sdk/conversation/impl/remote_conversation.py`, `openhands-sdk/openhands/sdk/workspace/remote/`, `openhands-sdk/openhands/sdk/conversation/conversation.py`, `openhands-sdk/openhands/sdk/workspace/workspace.py`, `openhands-sdk/openhands/sdk/workspace/models.py`, `openhands-sdk/openhands/sdk/tool/client_tool.py`, `openhands-sdk/openhands/sdk/tool/registry.py`, `openhands-sdk/openhands/sdk/llm/utils/telemetry.py`

Needs: `llm`, `tmux`, `git`

Routes: none

## Sub-features

- `F33.workspace-exec`: `Workspace(host=...)` returns a `RemoteWorkspace` whose `execute_command` returns the command's exit code, stdout and stderr, runs in the given `cwd`, gives up after `timeout` with exit `-1` and `timeout_occurred`; `alive` and `get_server_info()` match the server.
- `F33.workspace-bad-key`: with a wrong `api_key`, `execute_command` and `file_upload` report a 401 failure instead of raising, creating a conversation raises a 401 `HTTPStatusError`, and the server records no command, file or conversation.
- `F33.workspace-background`: `start_command` returns a command id at once, `get_command_output` shows no exit code while it runs, `stop_command` ends it (no later output), and stopping an unknown id is a no-op.
- `F33.workspace-files`: `file_upload` (from a path or from bytes) and `file_download` round-trip a binary file byte for byte; a missing remote file or a relative destination reports `success: false` with the server's 404 or 400 and leaves no local file.
- `F33.workspace-git`: `git_changes` (a path relative to `working_dir`) and `git_diff` return what the server's git routes return for a repository with a modified and an untracked file.
- `F33.workspace-settings`: `get_llm()` resolves the server's active LLM profile with a usable key, `get_llm(profile_name=...)` a named one (unknown names raise `FileNotFoundError`), `get_secrets()` returns `LookupSecret` references without values, and `get_mcp_config()` is empty on a fresh server.
- `F33.async-workspace`: `AsyncRemoteWorkspace` runs a command, starts, reads and stops a background command, uploads and downloads a file, reads git changes, a git diff and server info, and closes its client on context exit.
- `F33.workspace-runtime-scope`: a `RemoteWorkspace(runtime_conversation_id=...)` routes through `/api/conversations/{id}/...`: commands default to the conversation's workspace and land in its own bash history, paths outside that workspace are refused with 422, and an unknown conversation gives 404.
- `F33.workspace-runtime-lifecycle`: `get_runtime_session_key()` and `release_runtime()` hand out a scoped worker key and stop a conversation's runtime on a server with a Docker conversation runtime.
- `F33.conversation-create`: `Conversation(agent, workspace=RemoteWorkspace)` returns a `RemoteConversation` that creates the server conversation with the agent, working directory, tags and `max_iteration_per_run`; `send_message` stores the message without running and the agent's key is not echoed back.
- `F33.conversation-create-request`: `RemoteConversation.create(workspace, StartConversationRequest(...))` creates the conversation with the requested id from server-built `agent_settings`, tags and iteration limit, and runs its `initial_message`; repeating the same request returns the same conversation and adds no conversation or message.
- `F33.conversation-create-profile`: `RemoteConversation.create(workspace, StartConversationRequest(agent_profile_id=...))` builds the agent from a saved agent profile (its LLM profile, tool selection and system message suffix), records the launched profile and runs the `initial_message` to `finished`; `get_secrets(agent_profile_id=...)` returns only the profile's `secret_refs`.
- `F33.conversation-run`: `send_message` plus a blocking `run()` returns once the agent finished; the server reports `finished` and the file the agent wrote exists.
- `F33.conversation-callbacks`: callbacks receive the run's events live over the events WebSocket, and after `run()` the callback counts and `state.events` match the server's persisted events kind by kind.
- `F33.conversation-stats`: `conversation_stats` and `state.execution_status` read back the server's numbers (accumulated cost and prompt tokens of the agent's LLM).
- `F33.conversation-stop-hook`: with a `Stop` hook that refuses the first stop (exit 2), a blocking `run()` returns only after the extra agent turn the hook forced: the server is `finished` when `run()` returns, and `state.events` already holds both stop `HookExecutionEvent`s (blocked, then allowed) and the hook's feedback, matching the server kind by kind.
- `F33.client-tools`: `Conversation(client_tools=[ClientToolSpec(...)])` delivers the model's call to the callbacks as a typed `ClientAction_<name>` `ActionEvent`, the server acknowledges it with a `ClientToolObservation` and finishes, and a second program that attaches without `client_tools` replays those events.
- `F33.conversation-streaming`: with a `stream=True` agent LLM, callbacks receive the run's `StreamingDeltaEvent` frames live and the server stores none of them.
- `F33.conversation-token-callbacks`: `Conversation(token_callbacks=[...])` on a remote workspace calls the token callbacks with the run's streamed deltas.
- `F33.conversation-streaming-state`: streamed deltas stay out of `state.events`, which keeps matching the server's stored events.
- `F33.completion-logs`: an agent LLM with `log_completions=True` gets one log file per model call in its `log_completions_folder`, written by the client from the `LLMCompletionLogEvent`s the server streams and stores (the server writes no file).
- `F33.conversation-attach`: a second program reattaches by id with `RemoteConversation.attach()` or `Conversation(conversation_id=...)` and replays the same history without creating anything; `attach()` on an unknown id raises 404 while `Conversation(conversation_id=<new id>)` creates that id.
- `F33.conversation-attach-large`: attaching to a conversation with more than 100 stored events pages through the whole history: `state.events` holds the server's events in the server's order (plus the one unpersisted socket snapshot), and the first reads of `state.confirmation_policy`, `state.security_analyzer`, `state.hook_config` and `state.agent` equal the server's.
- `F33.conversation-state-reread`: reading `state.confirmation_policy`, `state.security_analyzer`, `state.agent` and `is_confirmation_mode_active` a second time gives the same answer as the first.
- `F33.conversation-ask-title`: `ask_agent()` answers without adding events, `generate_title()` returns a title within `max_length` without adding events, and `set_title()` changes the server's title.
- `F33.conversation-fork`: `fork(title=..., tags=...)` returns a `RemoteConversation` for a new server conversation with the same history, the title and the tags; `fork(agent=...)` is refused client-side.
- `F33.conversation-fork-branch`: `fork(from_event_id=..., reset_metrics=False)` returns a fork whose history ends at that event (its HEAD) with the source's stats, and `navigate_to(None)` and `navigate_to(event_id)` move the fork's HEAD without changing its events.
- `F33.conversation-plugins-tag`: `Conversation(plugins=[PluginSource(...)])` with inline URL credentials stores the `plugins` tag with the credentials masked, and the token reaches no server state.
- `F33.conversation-confirmation`: `set_confirmation_policy(AlwaysConfirm())` makes `run()` return at `waiting_for_confirmation`; `reject_pending_actions()` records a `UserRejectObservation`, never runs the action and leaves the conversation `idle`.
- `F33.conversation-pause`: `pause()` from another thread during a blocking `run()` lets the tool step in flight finish, makes `run()` return with `paused` and a `PauseEvent`, and a later `run()` resumes to `finished`.
- `F33.conversation-interrupt`: `interrupt()` from another thread while a blocking `run()` waits on a model call makes `run()` return at once without raising, at `paused` with one `InterruptEvent` and no `PauseEvent`, and a later `run()` reaches `finished`.
- `F33.conversation-run-timeout`: `run(timeout=...)` raises `ConversationRunError` wrapping a `TimeoutError` on time while the server keeps running, and a second `run()` joins the run in flight (the server's 409 is not an error) and returns at `finished`.
- `F33.conversation-run-join-long-tool`: a second `run()` also joins a run whose current tool call lasts longer than 30 s, instead of raising.
- `F33.conversation-stuck`: when the server's stuck detector stops a looping agent, `run()` raises `ConversationRunError` ("got stuck") at once and the server reports `stuck`.
- `F33.conversation-secrets`: `update_secrets()` with a plain value and with a `LookupSecret` from `get_secrets()` makes both available to the agent's commands, masked in observations and not stored in plaintext.
- `F33.conversation-run-error`: when the model provider rejects the key, `run()` raises `ConversationRunError` carrying the server's `ConversationErrorEvent` code, and the server reports `error`.
- `F33.ws-unavailable`: when the events socket cannot connect, `Conversation(...)` raises `WebSocketConnectionError` after `OPENHANDS_REMOTE_WS_READY_TIMEOUT` seconds (the conversation it created stays on the server); with `OPENHANDS_REMOTE_WS_READY_REQUIRED=false` it continues, and `run()` returns at `finished` through the REST fallback about 30 s after the server finished, with `state.events` matching the server.
- `F33.conversation-reconnect`: a live `RemoteConversation` survives a server restart: its events socket reconnects, a new message runs to `finished`, and `state.events` again matches the server.
- `F33.conversation-reconnect-callbacks`: an event the server records while a live `RemoteConversation`'s events socket is down reaches its callbacks once the socket is back, not only `state.events`.
- `F33.conversation-restart`: after a restart a new program reattaches to a finished conversation, sees the same history, and continues it to `finished`.
- `F33.custom-tool-module`: a tool registered in the client process is sent as `tool_module_qualnames`, imported by a server started with `OH_EXTRA_PYTHON_PATH`, and runs on the server when the agent calls it; a client-registered module the server cannot import is only a logged warning; reattaching from a process without the module raises `ImportError` naming the tool.
- `F33.custom-tool-module-unimportable`: a conversation whose agent names a tool from a module the server cannot import fails with a client error (a 4xx, or a recorded conversation error), never with a 500.
- `F33.conversation-close`: `close()` keeps the server conversation when `delete_on_close=False` and deletes it when the `Conversation(...)` factory's default `delete_on_close=True` applies.

## How to get to it (agent POV)

- SDK workspace: `Workspace(host=..., api_key=..., working_dir=...)` or
  `RemoteWorkspace(...)` (`openhands.sdk.workspace`): `execute_command`,
  `start_command`, `get_command_output`, `stop_command`, `file_upload`,
  `file_download`, `git_changes`, `git_diff`, `get_server_info`, `alive`,
  `get_llm`, `get_secrets`, `get_mcp_config`, and with
  `runtime_conversation_id` the conversation-scoped variants plus
  `get_runtime_session_key` and `release_runtime`. `AsyncRemoteWorkspace`
  mirrors the command, file, git and server-info methods with `await`.
- SDK conversation: `Conversation(agent=..., workspace=<RemoteWorkspace>, ...)`
  (factory; `delete_on_close` defaults to `True`) or `RemoteConversation(...)`
  (`delete_on_close` defaults to `False`), `RemoteConversation.create(workspace,
  StartConversationRequest)` (with `agent_settings` or `agent_profile_id`)
  and `RemoteConversation.attach(workspace, id)`; constructor options
  `hook_config`, `client_tools`, `callbacks`, `token_callbacks`, `plugins`,
  `tags`, `max_iteration_per_run`; then `send_message`,
  `run(blocking=..., timeout=...)`, `pause`, `interrupt`,
  `set_confirmation_policy`, `set_security_analyzer`,
  `reject_pending_actions`, `update_secrets`, `ask_agent`, `generate_title`,
  `set_title`, `fork(title, tags, from_event_id, reset_metrics)`,
  `navigate_to`, `close`, and the read side `state` (`execution_status`,
  `events`, `confirmation_policy`, `security_analyzer`, `hook_config`,
  `agent`, `refresh_from_server()`) and `conversation_stats`. Agent-side
  options that change what the client sees: an LLM with `stream=True`
  (`StreamingDeltaEvent` frames) or `log_completions=True` (completion log
  files written by the client), and tools registered in the client process
  (sent as `tool_module_qualnames`). Not driven here: `condense` and
  `load_plugin`, one-call wrappers over routes F10 and F09 drive.
- Wire: the workspace calls `/api/bash/*`, `/api/file/*`, `/api/git/*`,
  `/server_info`, `/health`, `/api/settings`, `/api/profiles/{name}`,
  `/api/settings/secrets` (or the `/api/conversations/{id}/...` scoped copies);
  the conversation calls `/api/conversations` and its sub-routes and keeps
  `WS /sockets/events/{conversation_id}` open with first-frame auth. Those
  routes belong to their own families; this family owns none.
- Configuration on the client: `OPENHANDS_REMOTE_WS_READY_TIMEOUT` (seconds to
  wait for the events socket, default 30) and
  `OPENHANDS_REMOTE_WS_READY_REQUIRED` (`false` continues without it), both
  driven by `F33.ws-unavailable`. On the server, `OH_EXTRA_PYTHON_PATH`
  makes custom tool modules importable (`F33.custom-tool-module`).
- Examples: `examples/02_remote_agent_server/` (01 local server with hooks,
  06 custom tool, 11 fork, 12 settings and secrets, 13 `get_llm`, 14 client
  tools); Agent Canvas and the TypeScript client reach the same routes
  without this SDK.
- Every recipe below runs a heredoc program with
  `control-agent-server exec -- .venv/bin/python -`, which injects
  `AGENT_SERVER_URL` and `SESSION_API_KEY` and passes `$DEEPSEEK_API_KEY`
  through, then checks the server with the CLI's `api` and `conversation
  events` verbs.

## Driving it with control-agent-server

Preconditions:

- A baseline run is live and exported (`launch --new`), `doctor` is ok,
  `$DEEPSEEK_API_KEY` is set, `tmux` and `git` are installed. The block below
  activates deepseek-flash on the server (the settings bullet reads it) and
  writes `$F33/f33.py`, a small helper every probe imports (via
  `--env PYTHONPATH="$F33"`): `workspace(name)` makes a `RemoteWorkspace` on
  `$F33/<name>` with the run's key (`host=` points it elsewhere),
  `agent()` a terminal-only agent on deepseek-flash with its own key
  (`usage_id` `qa-f33`; keyword arguments go to its `LLM`), `kinds(events)`
  counts events by class without `ConversationStateUpdateEvent`, and
  `emit(name, ...)` prints one JSON line and keeps it as `$F33/<name>.json`
  for the shell checks. `Proxy()` is a loopback TCP forwarder to the run's
  server for the socket-outage bullets: REST passes through, `block_ws`
  refuses event-socket upgrades with 403 (counted in `refused`), and
  `drop_sockets()` aborts the open event sockets as a network failure would.
  Each probe asserts with `assert`, so a failure exits non-zero and fails the
  bullet.
- It also creates a git repository fixture (`REPO`, one modified and one
  untracked file), the server secret `QA_F33_LOOKUP` (read by the settings
  and secrets bullets, deleted by the secrets bullet) and four `llm-stub`
  providers, each used by one bullet for provider behavior a real model
  cannot produce on demand: `STUB` answers every completion with 401
  (run-error), `LONG_STUB` asks for one exact `terminal` call of 45 s with a
  90 s tool timeout and then `finish` (run-join-long-tool), `HANG_STUB` holds
  the first completion for 300 s and answers `finish` next (`HANG_LOG` is its
  request log; interrupt), and `LOOP_STUB` repeats one `think` call forever
  (stuck). Probes on a stub call `set_title()` before the first message so
  that the server's auto-title, which uses the agent's LLM, does not consume
  a scripted step.
- The run bullet's conversation (`CID`) is reused read-only by the attach,
  ask/title, fork and fork-branch bullets and continued by the restart
  bullet; the create bullet's idle conversation (`IDLE`) is used by the
  runtime-scope bullet. Known-bug bullets read only what the normal bullet
  before them recorded (`stream.json`, `$BIG`, the module files)
  and set nothing later bullets use. The reconnect, restart and
  custom-tool-module bullets restart the run (the last one with
  `OH_EXTRA_PYTHON_PATH`, which later restarts keep).

  ```sh
  control-agent-server llm preset deepseek
  export F33="$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f33"
  export OPENHANDS_SUPPRESS_BANNER=1
  mkdir -p "$F33"
  cat > "$F33/f33.py" <<'EOF'
  """F33 probe helpers: this checkout's SDK against the run exec exports."""
  import asyncio
  import json
  import os
  import threading
  from collections import Counter
  from urllib.parse import urlparse

  from pydantic import SecretStr

  from openhands.sdk import LLM, Agent, Tool
  from openhands.sdk.workspace import RemoteWorkspace
  from openhands.tools.terminal import TerminalTool

  URL = os.environ["AGENT_SERVER_URL"]
  KEY = os.environ["SESSION_API_KEY"]
  DIR = os.environ["F33"]


  def workspace(name, key=KEY, host=URL, **kwargs):
      path = os.path.join(DIR, name)
      os.makedirs(path, exist_ok=True)
      return RemoteWorkspace(host=host, api_key=key, working_dir=path, **kwargs)


  def agent(**llm):
      llm.setdefault("model", "deepseek/deepseek-flash")
      llm.setdefault("api_key", SecretStr(os.environ.get("DEEPSEEK_API_KEY", "")))
      return Agent(llm=LLM(usage_id="qa-f33", **llm), tools=[Tool(name=TerminalTool.name)])


  def kinds(events):
      names = (type(e).__name__ for e in events)
      return dict(Counter(n for n in names if n != "ConversationStateUpdateEvent"))


  def emit(name, **result):
      line = json.dumps(result, default=str, sort_keys=True)
      with open(os.path.join(DIR, f"{name}.json"), "w") as f:
          f.write(line + "\n")
      print(line, flush=True)


  class Proxy:
      """Loopback TCP forwarder to the server that can refuse and drop event sockets."""

      def __init__(self, block_ws=False):
          self.block_ws, self.refused, self.sockets = block_ws, 0, set()
          self.upstream = urlparse(URL)
          self.loop = asyncio.new_event_loop()
          started = threading.Event()

          def serve():
              asyncio.set_event_loop(self.loop)
              server = self.loop.run_until_complete(asyncio.start_server(self._handle, "127.0.0.1", 0))
              self.url = "http://127.0.0.1:%d" % server.sockets[0].getsockname()[1]
              started.set()
              self.loop.run_forever()

          threading.Thread(target=serve, daemon=True).start()
          assert started.wait(10), "proxy did not start"

      async def _handle(self, reader, writer):
          try:
              head = await reader.readuntil(b"\r\n\r\n")
          except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
              writer.close()
              return
          upgrade = b"upgrade: websocket" in head.lower()
          if upgrade and self.block_ws:
              self.refused += 1
              writer.write(b"HTTP/1.1 403 Forbidden\r\ncontent-length: 0\r\nconnection: close\r\n\r\n")
              await writer.drain()
              writer.close()
              return
          up_reader, up_writer = await asyncio.open_connection(self.upstream.hostname, self.upstream.port)
          up_writer.write(head)
          pair = (writer, up_writer)
          if upgrade:
              self.sockets.add(pair)

          async def pipe(src, dst):
              try:
                  while data := await src.read(65536):
                      dst.write(data)
                      await dst.drain()
              except (ConnectionError, OSError):
                  pass
              finally:
                  dst.close()

          await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))
          self.sockets.discard(pair)

      def drop_sockets(self):
          def abort():
              for pair in list(self.sockets):
                  for side in pair:
                      side.transport.abort()

          self.loop.call_soon_threadsafe(abort)
  EOF
  REPO=$(control-agent-server fixture git-repo --name qa-f33-repo --print-path)
  export REPO
  STUB=$(control-agent-server fixture llm-stub --name qa-f33-stub --step status:401 --print-path)
  export STUB
  LONG_STUB=$(control-agent-server fixture llm-stub --name qa-f33-long \
    --step 'tool:terminal:{"command":"sleep 45; printf late > qa-f33-long.txt","timeout":90}' \
    --step 'tool:finish:{"message":"qa-f33 long done"}' --print-path)
  export LONG_STUB
  HANG_JSON=$(control-agent-server fixture llm-stub --name qa-f33-hang --step hang:300 --step 'tool:finish:{"message":"qa-f33 resumed"}')
  HANG_STUB=$(jq -r .url <<<"$HANG_JSON")
  HANG_LOG=$(jq -r .requests_log <<<"$HANG_JSON")
  export HANG_STUB HANG_LOG
  LOOP_STUB=$(control-agent-server fixture llm-stub --name qa-f33-loop --step 'tool:think:{"thought":"qa-f33 loop"}' --print-path)
  export LOOP_STUB
  control-agent-server api PUT /api/settings/secrets --json '{"name": "QA_F33_LOOKUP", "value": "qa-f33-lookup-value", "description": "qa f33 lookup"}' --expect 200
  ```

- **Commands, server info and liveness (`F33.workspace-exec`).** Run three
  commands through `RemoteWorkspace`, then read the server's bash history.
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.workspace-exec/probe -- .venv/bin/python - <<'PY'
  import os
  import time
  import f33
  from openhands.sdk.workspace import RemoteWorkspace, Workspace

  os.makedirs(os.path.join(f33.DIR, "exec"), exist_ok=True)
  ws = Workspace(host=f33.URL, api_key=f33.KEY, working_dir=os.path.join(f33.DIR, "exec"))
  assert isinstance(ws, RemoteWorkspace), type(ws)
  assert ws.alive and not RemoteWorkspace(host="http://127.0.0.1:9", working_dir="/tmp").alive
  r = ws.execute_command("echo qa-f33-out; echo qa-f33-err >&2; exit 3", cwd=ws.working_dir)
  assert (r.exit_code, r.stdout, r.stderr, r.timeout_occurred) == (3, "qa-f33-out\n", "qa-f33-err\n", False), r
  pwd = ws.execute_command("pwd", cwd=ws.working_dir)
  assert (pwd.exit_code, pwd.stdout) == (0, ws.working_dir + "\n"), pwd
  t0 = time.monotonic()
  slow = ws.execute_command("sleep 20", cwd=ws.working_dir, timeout=2)
  assert (slow.exit_code, slow.timeout_occurred) == (-1, True) and time.monotonic() - t0 < 10, slow
  f33.emit("exec", sha=ws.get_server_info()["build_git_sha"])
  PY
  control-agent-server api GET /server_info --auth none --check build_git_sha eq "$(jq -r .sha "$F33/exec.json")"
  control-agent-server api GET /api/bash/bash_events/search --query kind__eq=BashCommand --query sort_order=TIMESTAMP_DESC --query limit=3 \
    --check items.0.command eq 'sleep 20' --check items.0.timeout eq 2 --check items.1.command eq pwd \
    --check items.2.command contains qa-f33-out --check items.2.cwd eq "$F33/exec" --save F33.workspace-exec/server-commands
  control-agent-server api GET /api/bash/bash_events/search --query kind__eq=BashOutput --query sort_order=TIMESTAMP_DESC --query limit=3 \
    --check items.2.exit_code eq 3 --check items.2.stderr contains qa-f33-err --check items.1.exit_code eq 0 \
    --check items.0.exit_code eq -1 --until-ok 15 --save F33.workspace-exec/server-outputs
  ```
  The probe passes; the server's newest three commands are the SDK's, with
  the `cwd` and `timeout` it sent, and their outputs carry exit codes `3`,
  `0` and `-1` (the server stopped `sleep 20` after 2 s too).
- **Wrong session key (`F33.workspace-bad-key`).** The run's key works (the
  bullet above); a wrong one must change nothing.
  ```sh
  N0=$(control-agent-server api GET /api/conversations/count --field .)
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.workspace-bad-key/probe -- .venv/bin/python - <<'PY'
  import httpx
  import f33
  from openhands.sdk import Conversation

  bad = f33.workspace("badkey", key="qa-f33-wrong-key")
  r = bad.execute_command("echo qa-f33-bad-key", cwd=bad.working_dir)
  assert r.exit_code == -1 and "401" in r.stderr, r
  up = bad.file_upload(b"qa-f33-bad\n", bad.working_dir + "/qa-f33-bad.txt")
  assert not up.success and "401" in up.error, up
  try:
      Conversation(agent=f33.agent(), workspace=bad, visualizer=None)
      raise AssertionError("a conversation was created with a wrong key")
  except httpx.HTTPStatusError as e:
      assert e.response.status_code == 401, e
  f33.emit("badkey", stderr=r.stderr[:120])
  PY
  control-agent-server api GET /api/conversations/count --check . eq "$N0"
  control-agent-server api GET /api/bash/bash_events/search --query kind__eq=BashCommand --query sort_order=TIMESTAMP_DESC --query limit=1 \
    --check items.0.command not-contains qa-f33-bad-key --save F33.workspace-bad-key/no-command
  test ! -e "$F33/badkey/qa-f33-bad.txt"
  ```
  `execute_command` returns exit `-1` with `Remote execution error: Client
  error '401 Unauthorized'` in stderr (it never raises), the upload reports
  `success: false`, `Conversation(...)` raises; the count, the newest command
  and the workspace are unchanged.
- **Background command (`F33.workspace-background`).** Start a long command,
  stop it, read its output.
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.workspace-background/probe -- .venv/bin/python - <<'PY'
  import time
  import f33

  ws = f33.workspace("bg")
  cmd = ws.start_command("echo qa-f33-bg-start; sleep 60; echo qa-f33-bg-late", cwd=ws.working_dir, timeout=120)
  running = ws.get_command_output(cmd)
  assert running is None or running.get("exit_code") is None, running
  ws.stop_command(cmd)
  deadline = time.monotonic() + 30
  while (out := ws.get_command_output(cmd)) is None or out.get("exit_code") is None:
      assert time.monotonic() < deadline, out
      time.sleep(0.5)
  assert "qa-f33-bg-start" in out["stdout"] and "qa-f33-bg-late" not in out["stdout"], out
  ws.stop_command("0" * 32)
  f33.emit("bg", command_id=cmd, exit_code=out["exit_code"])
  PY
  BG=$(jq -r .command_id "$F33/bg.json")
  control-agent-server api GET /api/bash/bash_events/search --query kind__eq=BashOutput --query command_id__eq="$BG" \
    --check items.-1.exit_code eq "$(jq .exit_code "$F33/bg.json")" --check items.-1.exit_code ne 0 \
    --check items contains qa-f33-bg-start --check items not-contains qa-f33-bg-late --save F33.workspace-background/outputs
  ```
  `start_command` returns at once; the stopped command ends with a negative
  exit code (`-15`, SIGTERM) and only the output printed before the stop;
  the unknown id is ignored (the server's 404 is swallowed).
- **File round trip (`F33.workspace-files`).** Upload 70 kB of random bytes,
  download them, and compare with what the server serves.
  ```sh
  head -c 70000 /dev/urandom > "$F33/qa-f33-upload.bin"
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.workspace-files/probe -- .venv/bin/python - <<'PY'
  import os
  import f33

  ws = f33.workspace("files")
  src = os.path.join(f33.DIR, "qa-f33-upload.bin")
  remote = os.path.join(ws.working_dir, "sub", "qa-f33-upload.bin")
  up = ws.file_upload(src, remote)
  assert up.success and up.destination_path == remote, up
  down = ws.file_download(remote, os.path.join(f33.DIR, "qa-f33-download.bin"))
  assert down.success and down.file_size == os.path.getsize(src), down
  mem = ws.file_upload(b"qa-f33-bytes\n", os.path.join(ws.working_dir, "qa-f33-bytes.txt"))
  assert mem.success, mem
  missing = ws.file_download(os.path.join(ws.working_dir, "qa-f33-missing.bin"), os.path.join(f33.DIR, "qa-f33-missing.bin"))
  assert not missing.success and "404" in missing.error, missing
  relative = ws.file_upload(b"qa-f33-relative\n", "qa-f33-relative.txt")
  assert not relative.success and "400" in relative.error, relative
  f33.emit("files", remote=remote, size=down.file_size)
  PY
  cmp "$F33/qa-f33-upload.bin" "$F33/qa-f33-download.bin"
  control-agent-server api GET /api/file/download --query path="$F33/files/sub/qa-f33-upload.bin" --expect 200 \
    --raw-out "$F33/qa-f33-rest.bin" --quiet --save F33.workspace-files/rest-download
  cmp "$F33/qa-f33-upload.bin" "$F33/qa-f33-rest.bin"
  test "$(cat "$F33/files/qa-f33-bytes.txt")" = qa-f33-bytes
  test ! -e "$F33/qa-f33-missing.bin"
  test ! -e "$AGENT_SERVER_VERIFY_RUN/server/qa-f33-relative.txt"
  ```
  Both copies are identical to the source; the missing file gives a 404
  failure without a local file, and the relative destination a 400 without a
  file in the server's working directory (`<run>/server`, where a relative
  path would land).
- **Git changes and diff (`F33.workspace-git`).** Read the fixture
  repository through the SDK and through REST.
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.workspace-git/probe -- .venv/bin/python - <<'PY'
  import os
  import f33
  from openhands.sdk.workspace import RemoteWorkspace

  repo = os.environ["REPO"]
  ws = RemoteWorkspace(host=f33.URL, api_key=f33.KEY, working_dir=os.path.dirname(repo))
  changes = sorted((c.status.value, str(c.path)) for c in ws.git_changes(os.path.basename(repo)))
  assert changes == [("ADDED", "notes.txt"), ("UPDATED", "README.md")], changes
  diff = ws.git_diff(os.path.join(repo, "README.md"))
  assert "Modified, not committed." in diff.modified and "Created by control-agent-server." in diff.original, diff
  f33.emit("git", changes=changes, modified=diff.modified)
  PY
  control-agent-server api GET /api/git/changes --query path="$REPO" --check . len-eq 2 --check . contains README.md \
    --check . contains notes.txt --save F33.workspace-git/changes
  control-agent-server api GET /api/git/diff --query path="$REPO/README.md" --check modified eq "$(jq -r .modified "$F33/git.json")" \
    --save F33.workspace-git/diff
  ```
  The SDK joins the relative repository name with `working_dir`; both views
  list `README.md` as `UPDATED` and `notes.txt` as `ADDED` with the same diff.
- **LLM profiles, secrets and MCP from the server (`F33.workspace-settings`).**
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.workspace-settings/probe -- .venv/bin/python - <<'PY'
  import os
  import f33

  ws = f33.workspace("settings")
  llm = ws.get_llm()
  assert (llm.model, llm.usage_id) == ("deepseek/deepseek-flash", "profile:deepseek-flash"), llm
  assert llm.api_key.get_secret_value() == os.environ["DEEPSEEK_API_KEY"]
  pro = ws.get_llm(profile_name="deepseek-pro", temperature=0.0)
  assert (pro.model, pro.usage_id, pro.temperature) == ("deepseek/deepseek-v4-pro", "profile:deepseek-pro", 0.0), pro
  try:
      ws.get_llm(profile_name="qa-f33-missing")
      raise AssertionError("an unknown profile resolved")
  except FileNotFoundError:
      pass
  secrets = ws.get_secrets(names=["QA_F33_LOOKUP"])
  lookup = secrets["QA_F33_LOOKUP"]
  assert list(secrets) == ["QA_F33_LOOKUP"] and lookup.url == f33.URL + "/api/settings/secrets/QA_F33_LOOKUP", secrets
  assert "qa-f33-lookup-value" not in lookup.model_dump_json(context={"expose_secrets": True})
  assert ws.get_mcp_config() == {}
  f33.emit("settings", model=llm.model, url=lookup.url)
  PY
  control-agent-server api GET /api/settings --check active_profile eq deepseek-flash --save F33.workspace-settings/settings
  control-agent-server api GET /api/settings/secrets --check secrets contains QA_F33_LOOKUP --check . not-contains qa-f33-lookup-value
  ```
  `get_llm()` follows `active_profile` and carries the profile's real key;
  the lookup reference holds a URL and the session header, never the value.
- **Async workspace (`F33.async-workspace`).**
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.async-workspace/probe -- .venv/bin/python - <<'PY'
  import asyncio
  import os
  import f33
  from openhands.sdk.workspace import AsyncRemoteWorkspace


  async def main():
      path = os.path.join(f33.DIR, "async")
      os.makedirs(path, exist_ok=True)
      async with AsyncRemoteWorkspace(host=f33.URL, api_key=f33.KEY, working_dir=path) as ws:
          r = await ws.execute_command("printf qa-f33-async; pwd >&2", cwd=path)
          assert (r.exit_code, r.stdout, r.stderr) == (0, "qa-f33-async", path + "\n"), r
          up = await ws.file_upload(b"qa-f33-async-bytes", os.path.join(path, "a.txt"))
          down = await ws.file_download(os.path.join(path, "a.txt"), os.path.join(f33.DIR, "async-down.txt"))
          assert up.success and down.success and down.file_size == 18, (up, down)
          changes = sorted(str(c.path) for c in await ws.git_changes(os.environ["REPO"]))
          assert changes == ["README.md", "notes.txt"], changes
          diff = await ws.git_diff(os.path.join(os.environ["REPO"], "README.md"))
          assert "Modified, not committed." in diff.modified, diff
          bg = await ws.start_command("echo qa-f33-async-bg; sleep 60; echo qa-f33-async-late", cwd=path, timeout=120)
          running = await ws.get_command_output(bg)
          assert running is None or running.get("exit_code") is None, running
          await ws.stop_command(bg)
          for _ in range(60):
              out = await ws.get_command_output(bg)
              if out is not None and out.get("exit_code") is not None:
                  break
              await asyncio.sleep(0.5)
          assert out and "qa-f33-async-bg" in out["stdout"] and "qa-f33-async-late" not in out["stdout"], out
          await ws.stop_command("0" * 32)
          info = await ws.get_server_info()
      assert ws._client is None
      return info["build_git_sha"], bg, out["exit_code"]


  sha, bg, code = asyncio.run(main())
  f33.emit("async", sha=sha, command_id=bg, exit_code=code)
  PY
  test "$(cat "$F33/async-down.txt")" = qa-f33-async-bytes
  control-agent-server api GET /api/bash/bash_events/search --query kind__eq=BashCommand --query sort_order=TIMESTAMP_DESC --query limit=2 \
    --check items.1.command contains qa-f33-async --check items.1.cwd eq "$F33/async" \
    --check items.0.command contains qa-f33-async-bg --save F33.async-workspace/command
  control-agent-server api GET /api/bash/bash_events/search --query kind__eq=BashOutput --query command_id__eq="$(jq -r .command_id "$F33/async.json")" \
    --check items.-1.exit_code eq "$(jq .exit_code "$F33/async.json")" --check items.-1.exit_code ne 0 \
    --check items not-contains qa-f33-async-late --save F33.async-workspace/background
  ```
  Every awaited call succeeds: the diff matches the fixture, the background
  command is stopped (negative exit code, only the output printed before the
  stop), the unknown id is ignored, and the context exit drops the HTTP
  client.
- **Create a conversation without running it (`F33.conversation-create`).**
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.conversation-create/probe -- .venv/bin/python - <<'PY'
  import f33
  from openhands.sdk import Conversation, RemoteConversation

  conv = Conversation(agent=f33.agent(), workspace=f33.workspace("idle"), visualizer=None,
                      delete_on_close=False, tags={"qaf33": "idle"}, max_iteration_per_run=7)
  assert isinstance(conv, RemoteConversation), type(conv)
  conv.send_message("qa-f33 pending message")
  assert conv.state.execution_status.value == "idle"
  f33.emit("idle", cid=str(conv.id))
  conv.close()
  PY
  IDLE=$(jq -r .cid "$F33/idle.json")
  control-agent-server api GET "/api/conversations/$IDLE" --check execution_status eq idle --check workspace.working_dir eq "$F33/idle" \
    --check tags.qaf33 eq idle --check max_iterations eq 7 --check agent.llm.model eq deepseek/deepseek-flash \
    --check agent.llm.usage_id eq qa-f33 --check agent.llm.api_key ne "$DEEPSEEK_API_KEY" --save F33.conversation-create/get
  control-agent-server conversation events "$IDLE" --kinds MessageEvent --contains 'qa-f33 pending message' --expect-count 1
  control-agent-server conversation events "$IDLE" --kinds ActionEvent --expect-count 0
  ```
  The server stores the SDK's agent, working directory, tag and iteration
  limit; the message is persisted but nothing runs (`idle`, no action), and
  GET returns the LLM key redacted.
- **Create from a request, twice (`F33.conversation-create-request`).** The
  server builds the agent from `agent_settings`; the same request is then
  submitted again with the same `conversation_id`.
  ```sh
  N0=$(control-agent-server api GET /api/conversations/count --field .)
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.conversation-create-request/probe -- .venv/bin/python - <<'PY'
  import os
  import uuid
  import f33
  from openhands.sdk import RemoteConversation, TextContent
  from openhands.sdk.conversation.request import SendMessageRequest, StartConversationRequest
  from openhands.sdk.event import MessageEvent
  from openhands.sdk.workspace import LocalWorkspace

  ws = f33.workspace("request")
  cid = uuid.uuid4()
  request = StartConversationRequest(
      agent_settings={"agent_kind": "openhands", "tools": [{"name": "terminal"}],
                      "llm": {"model": "deepseek/deepseek-flash", "api_key": os.environ["DEEPSEEK_API_KEY"], "usage_id": "qa-f33-settings"}},
      workspace=LocalWorkspace(working_dir=ws.working_dir), conversation_id=cid, tags={"qaf33": "request"}, max_iterations=9,
      initial_message=SendMessageRequest(content=[TextContent(text="qa-f33 create: reply with one word")], run=True),
  )
  first = RemoteConversation.create(ws, request, visualizer=None)
  again = RemoteConversation.create(ws, request, visualizer=None)
  assert first.id == again.id == cid, (first.id, again.id, cid)
  assert (first.agent.llm.model, first.agent.llm.usage_id, first.max_iteration_per_run) == ("deepseek/deepseek-flash", "qa-f33-settings", 9)
  assert first.delete_on_close is False
  users = [e for e in again.state.events if isinstance(e, MessageEvent) and e.source == "user"]
  assert len(users) == 1, users
  f33.emit("request", cid=str(cid))
  first.close()
  again.close()
  PY
  REQ=$(jq -r .cid "$F33/request.json")
  control-agent-server conversation wait "$REQ" --until finished --timeout 300
  control-agent-server api GET /api/conversations/count --check . eq $((N0 + 1))
  control-agent-server api GET "/api/conversations/$REQ" --check execution_status eq finished --check tags.qaf33 eq request \
    --check max_iterations eq 9 --check agent.llm.usage_id eq qa-f33-settings --check workspace.working_dir eq "$F33/request" \
    --save F33.conversation-create-request/get
  control-agent-server api GET "/api/conversations/$REQ/events/search" --query source=user --check items len-eq 1 \
    --check items.0.llm_message.content.0.text eq 'qa-f33 create: reply with one word' --save F33.conversation-create-request/user-messages
  ```
  Both calls return the requested id; the agent (model, `usage_id`) and the
  iteration limit come back from the server; one conversation and one user
  message exist, and the run the server started for `initial_message`
  reaches `finished` (the server runs it whatever its `run` field says; see
  Gotchas).
- **Create from a saved agent profile (`F33.conversation-create-profile`).**
  The profile allows one of two server secrets, has no tools and a system
  message suffix, so its agent differs from the default one.
  ```sh
  control-agent-server api PUT /api/settings/secrets --json '{"name": "QA_F33_PROFILE_IN", "value": "qa-f33-in-value"}' --expect 200
  control-agent-server api PUT /api/settings/secrets --json '{"name": "QA_F33_PROFILE_OUT", "value": "qa-f33-out-value"}' --expect 200
  control-agent-server api POST /api/agent-profiles/qa-f33-profile --expect 201 \
    --json '{"llm_profile_ref": "deepseek-flash", "tools": [], "secret_refs": ["QA_F33_PROFILE_IN"], "system_message_suffix": "qa-f33 profile suffix"}'
  PROFILE=$(control-agent-server api GET /api/agent-profiles/qa-f33-profile --field profile.id)
  control-agent-server exec --env PYTHONPATH="$F33" --env PROFILE="$PROFILE" --save F33.conversation-create-profile/probe -- .venv/bin/python - <<'PY'
  import os
  import uuid
  import f33
  from openhands.sdk import RemoteConversation, TextContent
  from openhands.sdk.conversation.request import SendMessageRequest, StartConversationRequest
  from openhands.sdk.workspace import LocalWorkspace

  ws = f33.workspace("profile")
  profile = uuid.UUID(os.environ["PROFILE"])
  everything = ws.get_secrets()
  scoped = ws.get_secrets(agent_profile_id=str(profile))
  assert {"QA_F33_PROFILE_IN", "QA_F33_PROFILE_OUT"} <= set(everything) and list(scoped) == ["QA_F33_PROFILE_IN"], (everything, scoped)
  request = StartConversationRequest(agent_profile_id=profile, workspace=LocalWorkspace(working_dir=ws.working_dir), autotitle=False,
                                     initial_message=SendMessageRequest(content=[TextContent(text="Reply with one word: ready")], run=True))
  conv = RemoteConversation.create(ws, request, visualizer=None)
  assert (conv.agent.llm.model, conv.agent.tools) == ("deepseek/deepseek-flash", []), conv.agent
  conv.run(timeout=300)
  assert conv.state.execution_status.value == "finished", conv.state.execution_status
  f33.emit("profile", cid=str(conv.id))
  conv.close()
  PY
  PCID=$(jq -r .cid "$F33/profile.json")
  control-agent-server api GET "/api/conversations/$PCID" --check execution_status eq finished --check launched_agent_profile.agent_profile_id eq "$PROFILE" \
    --check launched_agent_profile.secret_refs len-eq 1 --check agent.tools len-eq 0 --check agent.llm.model eq deepseek/deepseek-flash \
    --save F33.conversation-create-profile/get
  control-agent-server conversation events "$PCID" --kinds SystemPromptEvent --contains 'qa-f33 profile suffix' --expect-count 1
  control-agent-server api GET /api/settings/secrets --query agent_profile_id="$PROFILE" --check secrets len-eq 1 \
    --check secrets.0.name eq QA_F33_PROFILE_IN --save F33.conversation-create-profile/scoped-secrets
  control-agent-server api DELETE /api/agent-profiles/qa-f33-profile --expect 200
  control-agent-server api DELETE /api/settings/secrets/QA_F33_PROFILE_IN --expect 200
  control-agent-server api DELETE /api/settings/secrets/QA_F33_PROFILE_OUT --expect 200
  ```
  `get_secrets()` lists both secrets and `get_secrets(agent_profile_id=...)`
  only the profile's `secret_refs`, as the server's scoped list does. The
  conversation's agent is the profile's (deepseek-flash, no tools, the
  suffix in its system prompt), the server records the launched profile, and
  the initial message runs to `finished` (`run()` joins it). The profile and
  both secrets are deleted afterwards.
- **Conversation-scoped workspace (`F33.workspace-runtime-scope`).** Bind a
  workspace to `IDLE` and probe its boundaries.
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33" --env CID="$IDLE" --save F33.workspace-runtime-scope/probe -- .venv/bin/python - <<'PY'
  import os
  import uuid
  import f33

  cid = uuid.UUID(os.environ["CID"])
  ws = f33.workspace("idle", runtime_conversation_id=cid)
  assert ws.api_prefix == f"/api/conversations/{cid}"
  r = ws.execute_command("pwd; echo qa-f33-scoped")
  assert (r.exit_code, r.stdout) == (0, ws.working_dir + "\nqa-f33-scoped\n"), r
  inside = ws.file_upload(b"qa-f33-in\n", os.path.join(ws.working_dir, "qa-f33-in.txt"))
  assert inside.success, inside
  outside = ws.file_upload(b"qa-f33-out\n", os.path.join(f33.DIR, "qa-f33-outside.txt"))
  assert not outside.success and "422" in outside.error, outside
  escape = ws.execute_command("pwd", cwd=f33.DIR)
  assert escape.exit_code == -1 and "422" in escape.stderr, escape
  gone = f33.workspace("idle", runtime_conversation_id=uuid.uuid4()).execute_command("true")
  assert gone.exit_code == -1 and "404" in gone.stderr, gone
  f33.emit("scope", prefix=ws.api_prefix)
  PY
  control-agent-server api GET "/api/conversations/$IDLE/bash/bash_events/search" --query kind__eq=BashCommand --check items len-eq 1 \
    --check items.0.command contains qa-f33-scoped --save F33.workspace-runtime-scope/scoped-history
  control-agent-server api GET /api/bash/bash_events/search --query kind__eq=BashCommand --query sort_order=TIMESTAMP_DESC --query limit=1 \
    --check items.0.command not-contains qa-f33-scoped
  test -f "$F33/idle/qa-f33-in.txt"
  test ! -e "$F33/qa-f33-outside.txt"
  ```
  Without `cwd` the scoped command runs in the conversation's workspace; it
  is in the conversation's bash history and not in the host's; the upload and
  the `cwd` outside the workspace are 422, an unknown conversation 404.
- **Runtime key and release (`F33.workspace-runtime-lifecycle`), blocked.**
  Needs an Agent Server whose conversation runtime is Docker (`docker` is not
  available on the machines this map was proven on).
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33" --env CID="$IDLE" --save F33.workspace-runtime-lifecycle/probe -- .venv/bin/python - <<'PY'
  import os
  import uuid
  import f33

  ws = f33.workspace("idle", runtime_conversation_id=uuid.UUID(os.environ["CID"]))
  key = ws.get_runtime_session_key()
  assert key and key != f33.KEY
  ws.release_runtime()
  f33.emit("lifecycle", released=True)
  PY
  control-agent-server api GET "/api/conversations/$IDLE" --expect 200
  ```
  On a Docker-runtime server the scoped key differs from the server key and
  the release stops the container while the conversation stays readable. On
  this local runtime the routes do not exist (see Gotchas).
- **Send and run to completion (`F33.conversation-run`, `F33.conversation-callbacks`, `F33.conversation-stats`).**
  The agent writes a file; the probe counts the events its callback received.
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.conversation-run/probe -- .venv/bin/python - <<'PY'
  from collections import Counter
  import f33
  from openhands.sdk import Conversation

  seen = Counter()
  conv = Conversation(agent=f33.agent(), workspace=f33.workspace("run"), visualizer=None, delete_on_close=False,
                      callbacks=[lambda e: seen.update([type(e).__name__])])
  conv.send_message("Create the file qa-f33-run.txt in the current directory containing exactly: hi")
  conv.run(timeout=300)
  assert conv.state.execution_status.value == "finished", conv.state.execution_status
  assert seen["ActionEvent"] >= 1 and seen["ObservationEvent"] >= 1 and seen["ConversationStateUpdateEvent"] >= 1, seen
  metrics = conv.conversation_stats.get_combined_metrics()
  assert metrics.accumulated_token_usage.prompt_tokens > 0, metrics
  f33.emit("run", cid=str(conv.id), seen=dict(seen), local=f33.kinds(conv.state.events),
           cost=metrics.accumulated_cost, prompt_tokens=metrics.accumulated_token_usage.prompt_tokens)
  conv.close()
  PY
  CID=$(jq -r .cid "$F33/run.json")
  test "$(cat "$F33/run/qa-f33-run.txt")" = hi
  control-agent-server api GET "/api/conversations/$CID" --check execution_status eq finished --check workspace.working_dir eq "$F33/run" \
    --check stats.usage_to_metrics.qa-f33.accumulated_cost eq "$(jq .cost "$F33/run.json")" \
    --check stats.usage_to_metrics.qa-f33.accumulated_token_usage.prompt_tokens eq "$(jq .prompt_tokens "$F33/run.json")" \
    --save F33.conversation-run/get
  for K in SystemPromptEvent MessageEvent ActionEvent ObservationEvent; do
    test "$(jq ".seen.$K // 0" "$F33/run.json")" -eq "$(jq ".local.$K // 0" "$F33/run.json")"
    control-agent-server conversation events "$CID" --kinds $K --expect-count "$(jq ".local.$K // 0" "$F33/run.json")"
  done
  control-agent-server conversation events "$CID" --kinds ObservationEvent --contains qa-f33-run.txt --expect-kind ObservationEvent \
    --save F33.conversation-callbacks/observations
  ```
  `run()` returns once the server is `finished`; the file holds `hi`; for
  each event kind the callback count, the `state.events` count and the
  server's count agree; cost and prompt tokens equal the server's `stats`.
- **A Stop hook that refuses the first stop (`F33.conversation-stop-hook`).**
  Hooks run on the server (`hook_config` travels in the create request). The
  hook counts its calls in the workspace and exits 2 on the first one, so the
  server reports `finished`, runs the hook, flips back to `running` and gives
  the agent one more turn with the hook's stderr as feedback. The probe reads
  the server's status the moment `run()` returns.
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.conversation-stop-hook/probe -- .venv/bin/python - <<'PY'
  import os
  import f33
  from openhands.sdk import Conversation
  from openhands.sdk.event import ActionEvent, HookExecutionEvent, MessageEvent
  from openhands.sdk.hooks import HookConfig, HookDefinition, HookMatcher

  ws = f33.workspace("stophook")
  calls = os.path.join(ws.working_dir, "qa-f33-stop-calls")
  command = (f"echo call >> {calls}; if [ $(wc -l < {calls}) -eq 1 ]; then "
             "echo 'qa-f33 stop hook: first create the file qa-f33-stop.txt containing exactly: again' >&2; exit 2; fi")
  conv = Conversation(agent=f33.agent(), workspace=ws, visualizer=None, delete_on_close=False,
                      hook_config=HookConfig(stop=[HookMatcher(hooks=[HookDefinition(command=command)])]))
  conv.send_message("Reply with one word: ready")
  conv.run(timeout=300)
  status = conv.state.refresh_from_server()["execution_status"]
  events = list(conv.state.events)
  stops = [i for i, e in enumerate(events) if isinstance(e, HookExecutionEvent) and e.hook_event_type == "Stop"]
  assert status == "finished", status
  assert [(events[i].blocked, events[i].exit_code) for i in stops] == [(True, 2), (False, 0)], [events[i] for i in stops]
  feedback = next(i for i, e in enumerate(events) if isinstance(e, MessageEvent) and e.source == "environment"
                  and "qa-f33 stop hook" in e.llm_message.content[0].text)
  assert stops[0] < feedback < stops[1], (stops, feedback)
  turns = [e for e in events[feedback:stops[1]] if isinstance(e, (ActionEvent, MessageEvent)) and e.source == "agent"]
  assert turns, events[feedback:]
  with open(calls) as f:
      assert f.read().count("call") == 2
  f33.emit("stophook", cid=str(conv.id), local=f33.kinds(events), turns_after_feedback=len(turns))
  conv.close()
  PY
  HOOK=$(jq -r .cid "$F33/stophook.json")
  control-agent-server api GET "/api/conversations/$HOOK" --check execution_status eq finished \
    --check hook_config.stop.0.hooks.0.command contains qa-f33-stop-calls --save F33.conversation-stop-hook/get
  control-agent-server conversation events "$HOOK" --kinds HookExecutionEvent --contains '"blocked": true' --expect-count 1 \
    --save F33.conversation-stop-hook/blocked
  control-agent-server conversation events "$HOOK" --kinds HookExecutionEvent --contains '"exit_code": 0' --expect-count 1
  for K in HookExecutionEvent MessageEvent ActionEvent ObservationEvent; do
    control-agent-server conversation events "$HOOK" --kinds $K --expect-count "$(jq ".local.$K // 0" "$F33/stophook.json")"
  done
  ```
  When `run()` returns the server already reports `finished`, and
  `state.events` holds the blocked stop (exit 2), the hook's feedback (an
  `environment` message), at least one agent turn after it, and the allowed
  stop (exit 0), with the same counts per kind as the server. A client that
  returned on the first `finished` status update would see `running` here
  and at most one hook event (the race `AGENTS.md` describes).
- **Client-defined tool (`F33.client-tools`).** The tool exists only as a
  JSON spec; the server injects it into the agent and acknowledges each call,
  and the client handles the call from its callback. A second program then
  attaches without `client_tools`.
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.client-tools/probe -- .venv/bin/python - <<'PY'
  import f33
  from openhands.sdk import Conversation
  from openhands.sdk.event import ActionEvent, ObservationEvent
  from openhands.sdk.tool.client_tool import ClientToolObservation, ClientToolSpec

  spec = ClientToolSpec(name="qa_f33_notify", description="Show a notification to the user (qa-f33 client tool).",
                        parameters={"type": "object", "properties": {"message": {"type": "string", "description": "Notification text"}},
                                    "required": ["message"]})
  calls = []
  conv = Conversation(agent=f33.agent(), workspace=f33.workspace("clienttool"), visualizer=None, delete_on_close=False, client_tools=[spec],
                      callbacks=[lambda e: isinstance(e, ActionEvent) and e.tool_name == "qa_f33_notify" and calls.append(e)])
  conv.send_message("Call the qa_f33_notify tool exactly once with the message qa-f33-ping, then finish.")
  conv.run(timeout=300)
  assert conv.state.execution_status.value == "finished", conv.state.execution_status
  assert len(calls) == 1, calls
  action = calls[0].action
  assert type(action).__name__ == "ClientAction_qa_f33_notify" and isinstance(action.model_dump()["message"], str), action
  acks = [e for e in conv.state.events if isinstance(e, ObservationEvent) and e.tool_name == "qa_f33_notify"]
  assert len(acks) == 1 and isinstance(acks[0].observation, ClientToolObservation), acks
  assert acks[0].action_id == calls[0].id, (acks[0].action_id, calls[0].id)
  f33.emit("clienttool", cid=str(conv.id), action=calls[0].id, args=action.model_dump(), local=f33.kinds(conv.state.events))
  conv.close()
  PY
  CT=$(jq -r .cid "$F33/clienttool.json")
  control-agent-server api GET "/api/conversations/$CT" --check execution_status eq finished --check client_tools.0.name eq qa_f33_notify \
    --check agent.tools contains qa_f33_notify --save F33.client-tools/get
  control-agent-server conversation events "$CT" --kinds ActionEvent --contains '"kind": "ClientAction_qa_f33_notify"' --expect-count 1 \
    --save F33.client-tools/action
  control-agent-server conversation events "$CT" --kinds ObservationEvent --contains '"kind": "ClientToolObservation"' --expect-count 1
  control-agent-server exec --env PYTHONPATH="$F33" --env CID="$CT" --save F33.client-tools/reattach -- .venv/bin/python - <<'PY'
  import json
  import os
  import uuid
  import f33
  from openhands.sdk import Conversation, RemoteConversation
  from openhands.sdk.event import ActionEvent

  cid = uuid.UUID(os.environ["CID"])
  first = json.load(open(os.path.join(f33.DIR, "clienttool.json")))
  saved = RemoteConversation.attach(f33.workspace("clienttool"), cid, visualizer=None)
  same = Conversation(agent=f33.agent(), workspace=f33.workspace("clienttool"), conversation_id=cid, visualizer=None, delete_on_close=False)
  for conv in (saved, same):
      acts = [e for e in conv.state.events if isinstance(e, ActionEvent) and e.tool_name == "qa_f33_notify"]
      assert [(e.id, type(e.action).__name__, e.action.model_dump()) for e in acts] == [
          (first["action"], "ClientAction_qa_f33_notify", first["args"])], acts
      assert f33.kinds(conv.state.events) == first["local"], f33.kinds(conv.state.events)
  f33.emit("clienttool-reattach", replayed=len(acts))
  saved.close()
  same.close()
  PY
  ```
  The callback receives exactly one `ActionEvent` whose action is the typed
  `ClientAction_qa_f33_notify` with a string `message`; the server answers
  it with a `ClientToolObservation` ("Tool call dispatched to client.") and
  the run finishes. A fresh process that never declared the tool replays the
  same action, typed, through both attach paths (the SDK registers the specs
  the server persisted before syncing events).
- **Streamed tokens (`F33.conversation-streaming`).** An agent LLM with
  `stream=True`, a token callback, and an event callback that keeps
  `StreamingDeltaEvent` frames. The token and `state.events` outcomes are
  recorded for the two known-bug bullets after this one.
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.conversation-streaming/probe -- .venv/bin/python - <<'PY'
  import f33
  from openhands.sdk import Conversation
  from openhands.sdk.event import StreamingDeltaEvent

  tokens, deltas = [], []
  conv = Conversation(agent=f33.agent(stream=True), workspace=f33.workspace("stream"), visualizer=None, delete_on_close=False,
                      token_callbacks=[tokens.append], callbacks=[lambda e: isinstance(e, StreamingDeltaEvent) and deltas.append(e)])
  conv.set_title("qa-f33 stream")
  conv.send_message("Reply with one short sentence about the sea.")
  conv.run(timeout=300)
  assert conv.state.execution_status.value == "finished", conv.state.execution_status
  assert deltas and any(d.content or d.reasoning_content for d in deltas), deltas
  f33.emit("stream", cid=str(conv.id), deltas=len(deltas), tokens=len(tokens), local=f33.kinds(conv.state.events))
  conv.close()
  PY
  STREAM=$(jq -r .cid "$F33/stream.json")
  control-agent-server api GET "/api/conversations/$STREAM" --check execution_status eq finished --check agent.llm.stream eq true \
    --save F33.conversation-streaming/get
  control-agent-server conversation events "$STREAM" --kinds StreamingDeltaEvent --expect-count 0 --save F33.conversation-streaming/stored
  control-agent-server api GET "/api/conversations/$STREAM" --check stats.usage_to_metrics.qa-f33.accumulated_token_usage.completion_tokens gt 0
  ```
  The callback receives the run's deltas live over the events socket
  (dozens for one sentence), and the server's stored events hold none: the
  deltas are transient frames, while the reply itself is stored as a
  `MessageEvent` (or `finish` action).
- **Token callbacks on a remote conversation (`F33.conversation-token-callbacks`), known bug.**
  Requires: `F33.conversation-streaming`
  The `Conversation(...)` factory forwards `token_callbacks` to
  `RemoteConversation`, whose constructor swallows it in `**_`, so the
  callbacks are never called although the server streamed the same tokens
  to the socket. The correct behavior asserted here is that they receive the
  run's deltas.
  ```sh
  test "$(jq .deltas "$F33/stream.json")" -gt 0
  test "$(jq .tokens "$F33/stream.json")" -gt 0  # bug
  ```
  The event callback got the deltas (control); today the token callback got
  `0` calls.
- **Deltas kept out of `state.events` (`F33.conversation-streaming-state`), known bug.**
  Requires: `F33.conversation-streaming`
  `RemoteConversation`'s default callback adds every socket frame to
  `state.events`, so the transient deltas become part of the client's
  history although the server never stores them. The correct behavior
  asserted here is that `state.events` keeps matching the server.
  ```sh
  test "$(jq '.local.MessageEvent // 0' "$F33/stream.json")" -eq "$(control-agent-server conversation events "$STREAM" --kinds MessageEvent | jq .matching)"
  test "$(jq '.local.StreamingDeltaEvent // 0' "$F33/stream.json")" -eq 0  # bug
  ```
  The stored kinds agree (control); today `state.events` also holds one
  `StreamingDeltaEvent` per delta (the same count as the callback saw).
- **Completion logs on the client (`F33.completion-logs`).** An agent LLM
  with `log_completions=True` and a relative `log_completions_folder`. The
  server replaces file logging with `LLMCompletionLogEvent`s; the client
  writes them under its own working directory, and the server's working
  directory (`<run>/server`) would show any file the server wrote itself.
  ```sh
  mkdir -p "$F33/logs-client"
  control-agent-server exec --cwd "$F33/logs-client" --env PYTHONPATH="$F33" --save F33.completion-logs/probe -- "$PWD/.venv/bin/python" - <<'PY'
  import os
  import f33
  from openhands.sdk import Conversation
  from openhands.sdk.event import LLMCompletionLogEvent

  seen = []
  conv = Conversation(agent=f33.agent(log_completions=True, log_completions_folder="qa-f33-logs"), workspace=f33.workspace("logs"),
                      visualizer=None, delete_on_close=False, callbacks=[lambda e: isinstance(e, LLMCompletionLogEvent) and seen.append(e)])
  conv.set_title("qa-f33 logs")
  conv.send_message("Reply with one word: ready")
  conv.run(timeout=300)
  assert conv.state.execution_status.value == "finished", conv.state.execution_status
  files = sorted(os.listdir("qa-f33-logs"))
  assert files and files == sorted(e.filename for e in seen), (files, [e.filename for e in seen])
  assert all(e.usage_id == "qa-f33" and e.model_name == "deepseek/deepseek-flash" for e in seen), seen
  f33.emit("logs", cid=str(conv.id), files=files)
  conv.close()
  PY
  LOGS=$(jq -r .cid "$F33/logs.json")
  NLOGS=$(jq '.files | length' "$F33/logs.json")
  control-agent-server conversation events "$LOGS" --kinds LLMCompletionLogEvent --expect-count "$NLOGS" --save F33.completion-logs/stored
  for NAME in $(jq -r '.files[]' "$F33/logs.json"); do
    control-agent-server conversation events "$LOGS" --kinds LLMCompletionLogEvent --contains "$NAME" --expect-count 1
    test -s "$F33/logs-client/qa-f33-logs/$NAME"
  done
  test ! -e "$AGENT_SERVER_VERIFY_RUN/server/qa-f33-logs"
  control-agent-server state grep deepseek-flash --glob 'fixtures/qa-f33/logs-client/qa-f33-logs/*'
  ```
  One file per model call lands in the client's `qa-f33-logs`, named after
  the event the socket delivered; the server stored the same events and
  wrote no file of its own. (Without `set_title()` the server's auto-title
  makes one more logged call on the agent's LLM, often after `run()`
  returned.)
- **Reattach from a second program (`F33.conversation-attach`).**
  ```sh
  N0=$(control-agent-server api GET /api/conversations/count --field .)
  control-agent-server exec --env PYTHONPATH="$F33" --env CID="$CID" --save F33.conversation-attach/probe -- .venv/bin/python - <<'PY'
  import os
  import uuid
  import httpx
  import f33
  from openhands.sdk import Conversation, RemoteConversation
  from openhands.sdk.event import MessageEvent

  cid = uuid.UUID(os.environ["CID"])
  ws = f33.workspace("run")
  saved = RemoteConversation.attach(ws, cid, visualizer=None)
  same = Conversation(agent=f33.agent(), workspace=ws, conversation_id=cid, visualizer=None, delete_on_close=False)
  assert saved.id == same.id == cid
  assert f33.kinds(saved.state.events) == f33.kinds(same.state.events)
  first = next(e for e in saved.state.events if isinstance(e, MessageEvent) and e.source == "user")
  assert "qa-f33-run.txt" in first.llm_message.content[0].text, first
  assert saved.state.execution_status.value == "finished" and saved.agent.llm.model == "deepseek/deepseek-flash"
  try:
      RemoteConversation.attach(ws, uuid.uuid4(), visualizer=None)
      raise AssertionError("attach accepted an unknown id")
  except httpx.HTTPStatusError as e:
      assert e.response.status_code == 404, e
  wanted = uuid.uuid4()
  made = Conversation(agent=f33.agent(), workspace=ws, conversation_id=wanted, visualizer=None, delete_on_close=False)
  assert made.id == wanted, (made.id, wanted)
  f33.emit("attach", kinds=f33.kinds(saved.state.events), made=str(made.id))
  for conv in (saved, same, made):
      conv.close()
  PY
  control-agent-server api GET /api/conversations/count --check . eq $((N0 + 1))
  control-agent-server api GET "/api/conversations/$(jq -r .made "$F33/attach.json")" --check execution_status eq idle \
    --save F33.conversation-attach/created-by-id
  for K in MessageEvent ActionEvent ObservationEvent; do
    control-agent-server conversation events "$CID" --kinds $K --expect-count "$(jq ".kinds.$K" "$F33/attach.json")"
  done
  ```
  Both reattach paths replay the same history (the first user message,
  `finished`, the saved agent) and create nothing; only the explicit new id
  adds one conversation.
- **Attach to a long history (`F33.conversation-attach-large`).** A seeding
  program sets a confirmation policy, a security analyzer and a
  `PreToolUse` hook, then queues 110 messages (no model call); the server
  stores more than 200 events. A second program attaches both ways and
  compares with the server's paged search.
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.conversation-attach-large/seed -- .venv/bin/python - <<'PY'
  import f33
  from openhands.sdk import Conversation
  from openhands.sdk.hooks import HookConfig, HookDefinition, HookMatcher
  from openhands.sdk.security.confirmation_policy import AlwaysConfirm
  from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer

  hooks = HookConfig(pre_tool_use=[HookMatcher(matcher="terminal", hooks=[HookDefinition(command="true # qa-f33 large")])])
  conv = Conversation(agent=f33.agent(), workspace=f33.workspace("large"), visualizer=None, delete_on_close=False, hook_config=hooks)
  conv.set_title("qa-f33 large")
  conv.set_confirmation_policy(AlwaysConfirm())
  conv.set_security_analyzer(LLMSecurityAnalyzer())
  for i in range(110):
      conv.send_message(f"qa-f33 seed {i:03d}")
  f33.emit("large", cid=str(conv.id))
  conv.close()
  PY
  BIG=$(jq -r .cid "$F33/large.json")
  control-agent-server conversation events "$BIG" --kinds MessageEvent --expect-count 110
  control-agent-server exec --env PYTHONPATH="$F33" --env CID="$BIG" --save F33.conversation-attach-large/probe -- .venv/bin/python - <<'PY'
  import os
  import uuid
  import httpx
  import f33
  from openhands.sdk import Conversation, RemoteConversation
  from openhands.sdk.event import ConversationStateUpdateEvent, MessageEvent

  cid = uuid.UUID(os.environ["CID"])
  server, page = [], None
  with httpx.Client(base_url=f33.URL, headers={"X-Session-API-Key": f33.KEY}) as http:
      while True:
          body = http.get(f"/api/conversations/{cid}/events/search", params={"limit": 100, **({"page_id": page} if page else {})}).json()
          server += [e["id"] for e in body["items"]]
          if not (page := body.get("next_page_id")):
              break
      info = http.get(f"/api/conversations/{cid}").json()
  assert len(server) > 200, len(server)
  for conv in (RemoteConversation.attach(f33.workspace("large"), cid, visualizer=None),
               Conversation(agent=f33.agent(), workspace=f33.workspace("large"), conversation_id=cid, visualizer=None, delete_on_close=False)):
      snapshots = [e for e in conv.state.events if isinstance(e, ConversationStateUpdateEvent) and e.key == "full_state"]
      ids = [e.id for e in conv.state.events if e not in snapshots]
      assert len(snapshots) == 1 and ids == server, (len(snapshots), len(ids), len(server))
      seeds = [e.llm_message.content[0].text for e in conv.state.events if isinstance(e, MessageEvent)]
      assert seeds == [f"qa-f33 seed {i:03d}" for i in range(110)], seeds[:3]
      state = conv.state
      assert state.confirmation_policy.model_dump() == info["confirmation_policy"], state.confirmation_policy
      assert state.security_analyzer.model_dump() == info["security_analyzer"], state.security_analyzer
      assert state.hook_config.model_dump(mode="json", by_alias=True) == info["hook_config"], state.hook_config
      agent = state.agent
      assert (agent.llm.model, [t.name for t in agent.tools]) == (info["agent"]["llm"]["model"], [t["name"] for t in info["agent"]["tools"]])
      conv.close()
  f33.emit("attachlarge", events=len(server))
  PY
  control-agent-server api GET "/api/conversations/$BIG" --check confirmation_policy.kind eq AlwaysConfirm \
    --check security_analyzer.kind eq LLMSecurityAnalyzer --check hook_config.pre_tool_use.0.hooks.0.command eq 'true # qa-f33 large' \
    --check execution_status eq idle --save F33.conversation-attach-large/get
  test "$(control-agent-server conversation events "$BIG" | jq .total)" -eq "$(jq .events "$F33/attachlarge.json")"
  ```
  The SDK pages the history 100 events at a time: `state.events` has every
  stored event in the server's order plus the one full-state snapshot the
  socket sent on connect (never persisted), the 110 messages in order, and
  the first read of each state field equals the server's GET.
- **Read the state twice (`F33.conversation-state-reread`), known bug.**
  Requires: `F33.conversation-attach-large`
  `RemoteState` validates its cached conversation info on every read, and
  the discriminated-union validator pops `kind` from the dict it is given
  (`openhands/sdk/utils/models.py`), so the first read of a polymorphic
  field strips the cache. The correct behavior asserted here is that a
  second read returns the same value.
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33" --env CID="$BIG" --save F33.conversation-state-reread/probe -- .venv/bin/python - <<'PY'
  import os
  import uuid
  import f33
  from openhands.sdk import RemoteConversation


  def read(conv, name):
      try:
          value = getattr(conv.state, name) if name != "is_confirmation_mode_active" else conv.is_confirmation_mode_active
      except Exception as e:  # recorded, compared below
          return f"error: {type(e).__name__}"
      return value if isinstance(value, bool) else type(value).__name__


  cid = uuid.UUID(os.environ["CID"])
  reads = {}
  for name in ("confirmation_policy", "security_analyzer", "agent", "is_confirmation_mode_active"):
      conv = RemoteConversation.attach(f33.workspace("large"), cid, visualizer=None)
      reads[name] = [read(conv, name), read(conv, name)]
      conv.close()
  assert reads["confirmation_policy"][0] == "AlwaysConfirm" and reads["security_analyzer"][0] == "LLMSecurityAnalyzer", reads
  assert reads["agent"][0] == "Agent" and reads["is_confirmation_mode_active"][0] is True, reads
  f33.emit("reread", reads=reads, stable=all(first == second for first, second in reads.values()))
  PY
  test "$(jq .stable "$F33/reread.json")" = true  # bug
  ```
  Each field is read on a freshly attached conversation, and every first
  read is right (control). Today the second read of `confirmation_policy`
  and `agent` raises `ValidationError` (`Unknown kind ''`), the second read
  of `security_analyzer` returns `None`, and so the second
  `is_confirmation_mode_active` is `False`. A full-state socket snapshot or
  `state.refresh_from_server()` restores the cache until the next read.
- **Ask, generate and set a title (`F33.conversation-ask-title`).**
  ```sh
  E0=$(control-agent-server api GET "/api/conversations/$CID/events/count" --field .)
  control-agent-server exec --env PYTHONPATH="$F33" --env CID="$CID" --save F33.conversation-ask-title/probe -- .venv/bin/python - <<'PY'
  import os
  import uuid
  import f33
  from openhands.sdk import Conversation

  conv = Conversation(agent=f33.agent(), workspace=f33.workspace("run"), conversation_id=uuid.UUID(os.environ["CID"]),
                      visualizer=None, delete_on_close=False)
  answer = conv.ask_agent("Answer with one word, yes or no: did you create a file?")
  assert isinstance(answer, str) and answer.strip(), answer
  title = conv.generate_title(max_length=40)
  assert isinstance(title, str) and 0 < len(title) <= 40, title
  conv.set_title("qa-f33 title")
  f33.emit("ask", answer=answer[:80], title=title)
  conv.close()
  PY
  control-agent-server api GET "/api/conversations/$CID/events/count" --check . eq "$E0"
  control-agent-server api GET "/api/conversations/$CID" --check title eq 'qa-f33 title' --check execution_status eq finished \
    --save F33.conversation-ask-title/get
  ```
  The answer and the generated title are non-empty; the event count is
  unchanged and the server's title is the one set.
- **Fork (`F33.conversation-fork`).**
  ```sh
  E0=$(control-agent-server api GET "/api/conversations/$CID/events/count" --field .)
  control-agent-server exec --env PYTHONPATH="$F33" --env CID="$CID" --save F33.conversation-fork/probe -- .venv/bin/python - <<'PY'
  import os
  import uuid
  import f33
  from openhands.sdk import Conversation, RemoteConversation

  src = Conversation(agent=f33.agent(), workspace=f33.workspace("run"), conversation_id=uuid.UUID(os.environ["CID"]),
                     visualizer=None, delete_on_close=False)
  try:
      src.fork(agent=f33.agent())
      raise AssertionError("fork accepted an agent")
  except NotImplementedError:
      pass
  fork = src.fork(title="qa-f33 fork", tags={"qaf33": "fork"})
  assert isinstance(fork, RemoteConversation) and fork.id != src.id
  assert f33.kinds(fork.state.events) == f33.kinds(src.state.events)
  f33.emit("fork", fork=str(fork.id), kinds=f33.kinds(fork.state.events))
  fork.close()
  src.close()
  PY
  FORK=$(jq -r .fork "$F33/fork.json")
  control-agent-server api GET "/api/conversations/$FORK" --check title eq 'qa-f33 fork' --check tags.qaf33 eq fork \
    --check execution_status eq idle --save F33.conversation-fork/get
  for K in MessageEvent ActionEvent ObservationEvent; do
    control-agent-server conversation events "$FORK" --kinds $K --expect-count "$(jq ".kinds.$K" "$F33/fork.json")"
  done
  control-agent-server api GET "/api/conversations/$CID/events/count" --check . eq "$E0" --save F33.conversation-fork/source-count
  ```
  The fork is a new `idle` conversation with the source's events, the title
  and the tag; the source keeps its own id and its event count.
- **Fork from an event, then move HEAD (`F33.conversation-fork-branch`).**
  Fork `CID` at its first user message, keeping the metrics.
  ```sh
  E0=$(control-agent-server api GET "/api/conversations/$CID/events/count" --field .)
  control-agent-server exec --env PYTHONPATH="$F33" --env CID="$CID" --save F33.conversation-fork-branch/probe -- .venv/bin/python - <<'PY'
  import os
  import uuid
  import f33
  from openhands.sdk import Conversation
  from openhands.sdk.event import ConversationStateUpdateEvent, MessageEvent


  def stored(conv):
      return [e for e in conv.state.events if not (isinstance(e, ConversationStateUpdateEvent) and e.key == "full_state")]


  src = Conversation(agent=f33.agent(), workspace=f33.workspace("run"), conversation_id=uuid.UUID(os.environ["CID"]),
                     visualizer=None, delete_on_close=False)
  events = stored(src)
  first = next(e for e in events if isinstance(e, MessageEvent) and e.source == "user")
  branch = src.fork(from_event_id=first.id, reset_metrics=False, title="qa-f33 branch")
  kept = stored(branch)
  assert [e.id for e in kept] == [e.id for e in events if not isinstance(e, ConversationStateUpdateEvent)][: len(kept)], kept
  assert kept[-1].id == first.id and len(kept) < len(events), (len(kept), len(events))
  assert branch.state.refresh_from_server()["leaf_event_id"] == first.id
  assert branch.conversation_stats.model_dump() == src.conversation_stats.model_dump()
  branch.navigate_to(None)
  assert branch.state._cached_state["leaf_event_id"] is None
  branch.navigate_to(first.id)
  assert branch.state._cached_state["leaf_event_id"] == first.id
  f33.emit("branch", fork=str(branch.id), first=first.id, kept=len(kept),
           cost=src.conversation_stats.usage_to_metrics["qa-f33"].accumulated_cost)
  branch.close()
  src.close()
  PY
  BRANCH=$(jq -r .fork "$F33/branch.json")
  control-agent-server api GET "/api/conversations/$BRANCH" --check title eq 'qa-f33 branch' --check leaf_event_id eq "$(jq -r .first "$F33/branch.json")" \
    --check stats.usage_to_metrics.qa-f33.accumulated_cost eq "$(jq .cost "$F33/branch.json")" --save F33.conversation-fork-branch/get
  control-agent-server api GET "/api/conversations/$BRANCH/events/count" --check . eq "$(jq .kept "$F33/branch.json")"
  control-agent-server api GET "/api/conversations/$CID/events/count" --check . eq "$E0"
  ```
  The branch holds the source's events up to and including the first user
  message (the system prompt and the message), its HEAD is that message,
  and its stats equal the source's; `navigate_to(None)` empties the HEAD and
  `navigate_to(<message id>)` restores it (the SDK refreshes the cached
  state, since `leaf_event_id` is not pushed on the socket). The source is
  unchanged.
- **Plugin source with inline credentials (`F33.conversation-plugins-tag`).**
  The plugin is lazy-loaded on the first run, so a conversation that never
  runs shows only what the create stored. No network.
  ```sh
  QA_F33_PLUGIN_TOKEN="qa-f33-plugin-token-$(date +%s%N)"
  export QA_F33_PLUGIN_TOKEN
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.conversation-plugins-tag/probe -- .venv/bin/python - <<'PY'
  import os
  import f33
  from openhands.sdk import Conversation
  from openhands.sdk.plugin import PluginSource

  token = os.environ["QA_F33_PLUGIN_TOKEN"]
  source = PluginSource(source=f"https://qa-f33-user:{token}@example.invalid/qa/plugin.git", ref="main")
  conv = Conversation(agent=f33.agent(), workspace=f33.workspace("plugins"), visualizer=None, delete_on_close=False,
                      plugins=[source], tags={"qaf33": "plugins"})
  info = conv.state.refresh_from_server()
  assert token not in str(info), "the plugin token is in the conversation info"
  f33.emit("plugins", cid=str(conv.id), tag=info["tags"]["plugins"])
  conv.close()
  PY
  PLUG=$(jq -r .cid "$F33/plugins.json")
  control-agent-server api GET "/api/conversations/$PLUG" --check tags.plugins eq 'https://****@example.invalid/qa/plugin.git' \
    --check tags.qaf33 eq plugins --check execution_status eq idle --save F33.conversation-plugins-tag/get
  printf %s "$QA_F33_PLUGIN_TOKEN" > "$F33/qa-f33-plugin-control.txt"
  control-agent-server state grep --env-value QA_F33_PLUGIN_TOKEN --glob 'fixtures/qa-f33/*'
  control-agent-server state grep --env-value QA_F33_PLUGIN_TOKEN --glob 'server/**/*' --expect-none
  control-agent-server state grep --env-value QA_F33_PLUGIN_TOKEN --glob 'home/**/*' --expect-none
  ```
  The factory adds a `plugins` tag next to the caller's tags, with the
  user and token replaced by `****`, and the token is in no file the server
  wrote (the control file shows the search finds it). The create request
  itself carries the masked URL too (`PluginSource` masks `source` on every
  dump), so a source that needs inline credentials cannot be fetched by the
  server (see Gotchas).
- **Confirm or reject (`F33.conversation-confirmation`).**
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.conversation-confirmation/probe -- .venv/bin/python - <<'PY'
  import f33
  from openhands.sdk import Conversation
  from openhands.sdk.security.confirmation_policy import AlwaysConfirm

  conv = Conversation(agent=f33.agent(), workspace=f33.workspace("confirm"), visualizer=None, delete_on_close=False)
  conv.set_confirmation_policy(AlwaysConfirm())
  conv.send_message("Run this exact shell command: printf no > qa-f33-confirm.txt")
  conv.run(timeout=300)
  assert conv.state.execution_status.value == "waiting_for_confirmation", conv.state.execution_status
  conv.reject_pending_actions("qa-f33 rejected")
  f33.emit("confirm", cid=str(conv.id))
  conv.close()
  PY
  CONF=$(jq -r .cid "$F33/confirm.json")
  control-agent-server api GET "/api/conversations/$CONF" --check confirmation_policy.kind eq AlwaysConfirm \
    --check execution_status eq idle --until-ok 30 --save F33.conversation-confirmation/get
  control-agent-server conversation events "$CONF" --kinds UserRejectObservation --contains 'qa-f33 rejected' --expect-count 1 \
    --save F33.conversation-confirmation/reject
  control-agent-server conversation events "$CONF" --kinds ObservationEvent --expect-count 0
  test ! -e "$F33/confirm/qa-f33-confirm.txt"
  ```
  `run()` returns at `waiting_for_confirmation` with the action pending; the
  rejection is recorded with its reason, nothing executed, and the
  conversation is `idle`.
- **Pause a blocking run (`F33.conversation-pause`).** `run()` blocks in a
  thread; the main thread pauses once the agent's first action arrives.
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.conversation-pause/probe -- .venv/bin/python - <<'PY'
  import threading
  import f33
  from openhands.sdk import Conversation
  from openhands.sdk.event import ActionEvent

  acted = threading.Event()
  conv = Conversation(agent=f33.agent(), workspace=f33.workspace("pause"), visualizer=None, delete_on_close=False,
                      callbacks=[lambda e: isinstance(e, ActionEvent) and acted.set()])
  conv.send_message("Run this exact shell command: sleep 8; printf done > qa-f33-pause.txt")
  runner = threading.Thread(target=conv.run, kwargs={"timeout": 300})
  runner.start()
  assert acted.wait(180), "no ActionEvent"
  conv.pause()
  runner.join(120)
  assert not runner.is_alive(), "run() did not return after pause()"
  paused = conv.state.execution_status.value
  assert paused == "paused", paused
  conv.run(timeout=300)
  assert conv.state.execution_status.value == "finished", conv.state.execution_status
  f33.emit("pause", cid=str(conv.id), paused=paused)
  conv.close()
  PY
  PAUSED=$(jq -r .cid "$F33/pause.json")
  test "$(cat "$F33/pause/qa-f33-pause.txt")" = done
  control-agent-server conversation events "$PAUSED" --kinds PauseEvent --expect-count 1 --save F33.conversation-pause/pause-event
  control-agent-server conversation events "$PAUSED" --key execution_status --contains paused --expect-min 1
  control-agent-server api GET "/api/conversations/$PAUSED" --check execution_status eq finished --save F33.conversation-pause/get
  ```
  The command in flight completes (its file exists), `run()` returns with
  `paused`, the server persisted one `PauseEvent` and a `paused` status, and
  the second `run()` finishes.
- **Interrupt a blocking run (`F33.conversation-interrupt`).** The agent's
  model is `HANG_STUB`, whose first completion hangs for 300 s; the main
  thread interrupts once the stub has logged that request.
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.conversation-interrupt/probe -- .venv/bin/python - <<'PY'
  import os
  import threading
  import time
  import f33
  from openhands.sdk import Conversation

  stub = f33.agent(model="openai/qa-stub", base_url=os.environ["HANG_STUB"] + "/v1", api_key="qa-stub", num_retries=0)
  conv = Conversation(agent=stub, workspace=f33.workspace("interrupt"), visualizer=None, delete_on_close=False)
  conv.set_title("qa-f33 interrupt")
  conv.send_message("qa-f33 interrupt me")
  errors = []


  def run():
      try:
          conv.run(timeout=300)
      except Exception as e:  # recorded, asserted below
          errors.append(repr(e))


  runner = threading.Thread(target=run)
  runner.start()
  deadline = time.monotonic() + 60
  while not open(os.environ["HANG_LOG"]).read().strip():
      assert time.monotonic() < deadline, "the model call never started"
      time.sleep(0.2)
  t0 = time.monotonic()
  conv.interrupt()
  runner.join(60)
  returned = time.monotonic() - t0
  assert not runner.is_alive(), "run() did not return after interrupt()"
  assert not errors, errors
  interrupted = conv.state.execution_status.value
  assert interrupted == "paused" and returned < 15, (interrupted, returned)
  conv.run(timeout=120)
  assert conv.state.execution_status.value == "finished", conv.state.execution_status
  f33.emit("interrupt", cid=str(conv.id), returned=round(returned, 1), interrupted=interrupted)
  conv.close()
  PY
  INT=$(jq -r .cid "$F33/interrupt.json")
  control-agent-server conversation events "$INT" --kinds InterruptEvent --expect-count 1 --save F33.conversation-interrupt/interrupt-event
  control-agent-server conversation events "$INT" --kinds PauseEvent --expect-count 0
  control-agent-server conversation events "$INT" --key execution_status --contains paused --expect-min 1
  control-agent-server api GET "/api/conversations/$INT" --check execution_status eq finished --save F33.conversation-interrupt/get
  control-agent-server sink read --name qa-f33-hang --path /v1/chat/completions --expect-min 2 --expect-max 2
  ```
  `interrupt()` cancels the hanging model call: the blocking `run()` returns
  within a second or two, without raising, at `paused`; the server recorded
  one `InterruptEvent` (no `PauseEvent`), and the second `run()` sends the
  stub's next completion (`finish`) and returns at `finished`. The stub saw
  exactly the two agent calls (no title request took a step).
- **Time out, then wait again (`F33.conversation-run-timeout`).** The agent's
  command outlives `run(timeout=3)`; a second `run()` waits for that same run.
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.conversation-run-timeout/probe -- .venv/bin/python - <<'PY'
  import time
  import f33
  from openhands.sdk import Conversation
  from openhands.sdk.conversation.exceptions import ConversationRunError

  conv = Conversation(agent=f33.agent(), workspace=f33.workspace("timeout"), visualizer=None, delete_on_close=False)
  conv.send_message("Run this exact shell command: sleep 15; printf late > qa-f33-timeout.txt")
  t0 = time.monotonic()
  try:
      conv.run(timeout=3)
      raise AssertionError("run() returned before its timeout")
  except ConversationRunError as e:
      assert isinstance(e.original_exception, TimeoutError), repr(e.original_exception)
  waited = time.monotonic() - t0
  assert 3 <= waited < 10, waited
  status = conv.state.refresh_from_server()["execution_status"]
  assert status == "running", status
  conv.run(timeout=300)
  assert conv.state.execution_status.value == "finished", conv.state.execution_status
  f33.emit("timeout", cid=str(conv.id), waited=waited, status_after_timeout=status)
  conv.close()
  PY
  TOUT=$(jq -r .cid "$F33/timeout.json")
  test "$(cat "$F33/timeout/qa-f33-timeout.txt")" = late
  control-agent-server api GET "/api/conversations/$TOUT" --check execution_status eq finished --save F33.conversation-run-timeout/get
  control-agent-server api GET "/api/conversations/$TOUT/events/search" --query source=user --check items len-eq 1
  ```
  The first `run()` raises after 3 to 10 s while the server still reports
  `running`; the second `run()` posts `/run`, gets 409 (already running),
  keeps waiting and returns at `finished`; the file the 15 s command wrote
  exists and the conversation has its one user message.
- **Join a run during a long tool call (`F33.conversation-run-join-long-tool`), known bug.**
  The same join as above, but the agent's command runs 45 s. The server's
  `/run` reads the status under the conversation's state lock, which the run
  loop holds for the whole tool call (`event_service.py`
  `_get_execution_status_sync`, see `F05.pause-tool-step`), and the SDK gives
  the run trigger a 30 s HTTP timeout (`remote_conversation.py`, `run()`), so
  today the second `run()` raises `ConversationRunError` wrapping
  `ReadTimeout` after 30 s while the server run goes on and finishes. The
  agent's model is `LONG_STUB`, which asks for exactly that `terminal` call
  with a 90 s tool timeout: a real model chooses its own `timeout`, and
  without one the terminal tool returns after 30 s of silence, which races
  the SDK's own 30 s. The probe records how the second `run()` ended; the
  correct behavior asserted here is that it waits and returns at `finished`.
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.conversation-run-join-long-tool/probe -- .venv/bin/python - <<'PY'
  import os
  import threading
  import time
  import f33
  from openhands.sdk import Conversation
  from openhands.sdk.conversation.exceptions import ConversationRunError
  from openhands.sdk.event import ActionEvent

  acted = threading.Event()
  stub = f33.agent(model="openai/qa-stub", base_url=os.environ["LONG_STUB"] + "/v1", api_key="qa-stub", num_retries=0)
  conv = Conversation(agent=stub, workspace=f33.workspace("longtool"), visualizer=None, delete_on_close=False,
                      callbacks=[lambda e: isinstance(e, ActionEvent) and e.tool_name == "terminal" and acted.set()])
  conv.set_title("qa-f33 long tool")
  conv.send_message("qa-f33 long tool call")
  conv.run(blocking=False)
  assert acted.wait(60), "no terminal ActionEvent"
  t0 = time.monotonic()
  try:
      conv.run(timeout=300)
      error = None
  except ConversationRunError as e:
      error = repr(e.original_exception)
  status = conv.state.execution_status.value
  f33.emit("longtool", cid=str(conv.id), waited=round(time.monotonic() - t0, 1), error=error, status=status,
           joined=error is None and status == "finished")
  conv.close()
  PY
  LONG=$(jq -r .cid "$F33/longtool.json")
  control-agent-server conversation wait "$LONG" --until finished --timeout 120
  test "$(cat "$F33/longtool/qa-f33-long.txt")" = late
  control-agent-server conversation events "$LONG" --kinds ActionEvent --contains '"timeout": 90.0' --expect-count 1
  control-agent-server api GET "/api/conversations/$LONG" --check execution_status eq finished --save F33.conversation-run-join-long-tool/get
  test "$(jq .joined "$F33/longtool.json")" = true  # bug
  ```
  `run(blocking=False)` returns at once; once the 45 s command is running,
  the blocking `run()` should wait for it and return at `finished`. Today
  the probe records `ReadTimeout('timed out')` after 30.0 s with the
  conversation still `running`; the server run goes on, the file appears and
  the conversation finishes.
- **A stuck agent (`F33.conversation-stuck`).** The agent's model is
  `LOOP_STUB`, which repeats the same `think` call, so the server's stuck
  detector stops the run.
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.conversation-stuck/probe -- .venv/bin/python - <<'PY'
  import os
  import time
  import f33
  from openhands.sdk import Conversation
  from openhands.sdk.conversation.exceptions import ConversationRunError

  stub = f33.agent(model="openai/qa-stub", base_url=os.environ["LOOP_STUB"] + "/v1", api_key="qa-stub", num_retries=0)
  conv = Conversation(agent=stub, workspace=f33.workspace("stuck"), visualizer=None, delete_on_close=False)
  conv.set_title("qa-f33 stuck")
  conv.send_message("qa-f33 loop forever")
  t0 = time.monotonic()
  try:
      conv.run(timeout=120)
      raise AssertionError("run() returned although the agent is stuck")
  except ConversationRunError as e:
      detail = str(e.original_exception)
      assert e.conversation_error is None and "stuck" in detail, (detail, e.conversation_error)
  waited = time.monotonic() - t0
  assert waited < 30, waited
  f33.emit("stuck", cid=str(conv.id), waited=round(waited, 1), detail=detail)
  conv.close()
  PY
  STUCK=$(jq -r .cid "$F33/stuck.json")
  control-agent-server api GET "/api/conversations/$STUCK" --check execution_status eq stuck --save F33.conversation-stuck/get
  control-agent-server conversation events "$STUCK" --kinds ActionEvent --contains qa-f33 --expect-min 3
  control-agent-server conversation events "$STUCK" --kinds ConversationErrorEvent --expect-count 0
  ```
  The detector stops the loop after a few identical `think` calls; `run()`
  raises `ConversationRunError` ("Remote conversation got stuck") within
  seconds, with no `conversation_error` (no `ConversationErrorEvent` is
  recorded for a stuck run), and the server reports `stuck`.
- **Secrets for the agent (`F33.conversation-secrets`).** A fresh plain value
  and the server secret `QA_F33_LOOKUP` by reference; the agent stores only
  hash prefixes, since models refuse to echo secrets.
  ```sh
  QA_F33_TOKEN="qa-f33-$(date +%s%N)"
  export QA_F33_TOKEN
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.conversation-secrets/probe -- .venv/bin/python - <<'PY'
  import os
  import f33
  from openhands.sdk import Conversation
  from openhands.sdk.event import ObservationEvent

  value = os.environ["QA_F33_TOKEN"]
  seen = []
  ws = f33.workspace("secret")
  conv = Conversation(agent=f33.agent(), workspace=ws, visualizer=None, delete_on_close=False,
                      callbacks=[lambda e: isinstance(e, ObservationEvent) and seen.append(e.model_dump_json())])
  conv.update_secrets({"QA_F33_TOKEN": value, **ws.get_secrets(names=["QA_F33_LOOKUP"])})
  conv.send_message('Run this exact shell command, which stores only hash prefixes, then stop: '
                    'for v in "$QA_F33_TOKEN" "$QA_F33_LOOKUP"; do printf %s "$v" | sha256sum | cut -c1-16; done > qa-f33-secret.sha')
  conv.run(timeout=300)
  assert seen and not any(value in o or "qa-f33-lookup-value" in o for o in seen), seen
  f33.emit("secret", cid=str(conv.id), observations=len(seen))
  conv.close()
  PY
  SEC=$(jq -r .cid "$F33/secret.json")
  { printf %s "$QA_F33_TOKEN" | sha256sum | cut -c1-16; printf %s qa-f33-lookup-value | sha256sum | cut -c1-16; } > "$F33/qa-f33-expected.sha"
  cmp "$F33/qa-f33-expected.sha" "$F33/secret/qa-f33-secret.sha"
  control-agent-server conversation events "$SEC" --kinds ObservationEvent --expect-min 1 --save F33.conversation-secrets/observations
  printf %s "$QA_F33_TOKEN" > "$F33/qa-f33-grep-control.txt"
  control-agent-server state grep --env-value QA_F33_TOKEN --glob 'fixtures/qa-f33/*'
  control-agent-server state grep --env-value QA_F33_TOKEN --glob 'server/workspace/conversations/**/*' --expect-none
  env QA_F33_SESSION_KEY="$(cat "$AGENT_SERVER_VERIFY_RUN/private/session_api_key")" \
    control-agent-server state grep --env-value QA_F33_SESSION_KEY --glob 'server/workspace/conversations/**/*' --expect-none
  control-agent-server api DELETE /api/settings/secrets/QA_F33_LOOKUP --expect 200
  ```
  Both hashes match, so the agent's shell saw both values; no observation
  carries either value. The grep finds the value in a control file (positive
  control), but neither the plain value nor the session key the
  `LookupSecret` sends as a header is on disk in plaintext in the
  conversation store. The server secret is deleted afterwards.
- **Provider error surfaces (`F33.conversation-run-error`).** The stub answers
  401 like a provider rejecting the key.
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.conversation-run-error/probe -- .venv/bin/python - <<'PY'
  import os
  import time
  import f33
  from openhands.sdk import Conversation
  from openhands.sdk.conversation.exceptions import ConversationRunError

  conv = Conversation(agent=f33.agent(model="openai/qa-stub", base_url=os.environ["STUB"] + "/v1", api_key="qa-stub", num_retries=0),
                      workspace=f33.workspace("error"), visualizer=None, delete_on_close=False)
  conv.send_message("hello")
  t0 = time.monotonic()
  try:
      conv.run(timeout=120)
      raise AssertionError("run() returned although the provider failed")
  except ConversationRunError as e:
      assert e.conversation_error is not None and e.conversation_error.code == "LLMAuthenticationError", e
  assert time.monotonic() - t0 < 60, time.monotonic() - t0
  assert conv.state.execution_status.value == "error"
  f33.emit("error", cid=str(conv.id))
  conv.close()
  PY
  ERR=$(jq -r .cid "$F33/error.json")
  control-agent-server api GET "/api/conversations/$ERR" --check execution_status eq error --save F33.conversation-run-error/get
  control-agent-server conversation events "$ERR" --kinds ConversationErrorEvent --contains LLMAuthenticationError --expect-min 1
  control-agent-server sink read --name qa-f33-stub --path /v1/chat/completions --expect-min 1
  ```
  `run()` raises within seconds with the structured error the server
  recorded; the stub saw the server's model request.
- **No events socket (`F33.ws-unavailable`).** The probe reaches the
  server through `f33.Proxy(block_ws=True)`: REST passes, every event-socket
  upgrade is refused with 403. First with the default settings and a 3 s
  readiness timeout, then with `OPENHANDS_REMOTE_WS_READY_REQUIRED=false`; a
  watcher thread polls the server directly to time `run()`'s return against
  the moment the server reported `finished`.
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.ws-unavailable/probe -- .venv/bin/python - <<'PY'
  import os
  import threading
  import time
  import httpx
  import f33
  from openhands.sdk import Conversation
  from openhands.sdk.conversation.exceptions import WebSocketConnectionError

  proxy = f33.Proxy(block_ws=True)
  ws = f33.workspace("nows", host=proxy.url)
  assert ws.alive, "REST does not pass the proxy"
  os.environ["OPENHANDS_REMOTE_WS_READY_TIMEOUT"] = "3"
  t0 = time.monotonic()
  try:
      Conversation(agent=f33.agent(), workspace=ws, visualizer=None, delete_on_close=False)
      raise AssertionError("a conversation connected without its events socket")
  except WebSocketConnectionError as e:
      refused_cid, ready_wait = str(e.conversation_id), time.monotonic() - t0
  assert 3 <= ready_wait < 10 and proxy.refused >= 1, (ready_wait, proxy.refused)
  os.environ["OPENHANDS_REMOTE_WS_READY_REQUIRED"] = "false"
  seen = []
  conv = Conversation(agent=f33.agent(), workspace=ws, visualizer=None, delete_on_close=False, callbacks=[seen.append])
  conv.set_title("qa-f33 no socket")
  conv.send_message("Reply with one word: ready")
  finished_at = []


  def watch():
      with httpx.Client(base_url=f33.URL, headers={"X-Session-API-Key": f33.KEY}) as http:
          while not finished_at:
              if http.get(f"/api/conversations/{conv.id}").json()["execution_status"] == "finished":
                  finished_at.append(time.monotonic())
              time.sleep(0.25)


  threading.Thread(target=watch, daemon=True).start()
  conv.run(timeout=300)
  lag = time.monotonic() - finished_at[0]
  assert conv.state.execution_status.value == "finished", conv.state.execution_status
  assert 29 <= lag <= 40, lag
  assert not seen and not proxy.sockets, (len(seen), proxy.sockets)
  f33.emit("nows", refused_cid=refused_cid, ready_wait=round(ready_wait, 1), cid=str(conv.id), lag=round(lag, 1),
           local=f33.kinds(conv.state.events))
  conv.close()
  PY
  NOWS=$(jq -r .cid "$F33/nows.json")
  control-agent-server api GET "/api/conversations/$(jq -r .refused_cid "$F33/nows.json")" --check execution_status eq idle \
    --save F33.ws-unavailable/left-behind
  control-agent-server api GET "/api/conversations/$NOWS" --check execution_status eq finished --save F33.ws-unavailable/get
  for K in SystemPromptEvent MessageEvent ActionEvent ObservationEvent; do
    control-agent-server conversation events "$NOWS" --kinds $K --expect-count "$(jq ".local.$K // 0" "$F33/nows.json")"
  done
  ```
  With the socket required, `Conversation(...)` raises
  `WebSocketConnectionError` after the 3 s timeout, but the conversation it
  created first stays on the server (`idle`). With
  `OPENHANDS_REMOTE_WS_READY_REQUIRED=false` it continues without the
  socket: callbacks get nothing, and `run()` waits for the post-run socket
  snapshot that never comes, then accepts the REST status 30 s after the
  server reported `finished` (the hard fallback), reconciles, and
  `state.events` matches the server kind by kind.
- **A live conversation across a restart (`F33.conversation-reconnect`).** The
  probe runs in the background, signals `reconnect-ready`, and continues once
  the shell has restarted the server and created `reconnect-go`.
  ```sh
  rm -f "$F33/reconnect-ready" "$F33/reconnect-go"
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.conversation-reconnect/probe -- .venv/bin/python - > "$F33/reconnect-exec.json" <<'PY' &
  import os
  import time
  from collections import Counter
  import f33
  from openhands.sdk import Conversation

  seen = Counter()
  conv = Conversation(agent=f33.agent(), workspace=f33.workspace("reconnect"), visualizer=None, delete_on_close=False,
                      callbacks=[lambda e: seen.update([type(e).__name__])])
  before = seen["ConversationStateUpdateEvent"]
  open(os.path.join(f33.DIR, "reconnect-ready"), "w").write(str(conv.id))
  deadline = time.monotonic() + 300
  while not os.path.exists(os.path.join(f33.DIR, "reconnect-go")):
      assert time.monotonic() < deadline, "no reconnect-go"
      time.sleep(0.2)
  conv.send_message("Run this exact shell command: printf back > qa-f33-reconnect.txt")
  conv.run(timeout=300)
  assert conv.state.execution_status.value == "finished", conv.state.execution_status
  assert seen["ConversationStateUpdateEvent"] > before, seen
  f33.emit("reconnect", cid=str(conv.id), local=f33.kinds(conv.state.events), seen=dict(seen))
  conv.close()
  PY
  PROBE=$!
  for i in $(seq 240); do test -e "$F33/reconnect-ready" && break; sleep 0.5; done
  test -e "$F33/reconnect-ready"
  control-agent-server restart
  touch "$F33/reconnect-go"
  wait "$PROBE"
  RECON=$(jq -r .cid "$F33/reconnect.json")
  test "$(cat "$F33/reconnect/qa-f33-reconnect.txt")" = back
  for K in SystemPromptEvent MessageEvent ActionEvent ObservationEvent; do
    control-agent-server conversation events "$RECON" --kinds $K --expect-count "$(jq ".local.$K // 0" "$F33/reconnect.json")"
  done
  control-agent-server api GET "/api/conversations/$RECON" --check execution_status eq finished --save F33.conversation-reconnect/get
  ```
  The same `RemoteConversation` object, created before the restart, runs the
  new message to `finished`; its socket delivered state updates again, and
  after `run()` its `state.events` matches the server kind by kind.
- **Callbacks after a reconnect (`F33.conversation-reconnect-callbacks`), known bug.**
  The SDK's events socket retries on a backoff (1 s doubling to 30 s) and
  resubscribes without `resend_mode`; on reconnect it only merges missed
  events into `state.events` (`on_reconnect=reconcile`), so callbacks never
  see what the server recorded in between. The probe talks to the server
  through `f33.Proxy()`, so the outage is exact instead of racing a
  restart's timing: a first message sent while connected reaches the
  callback (control); then the proxy drops the socket and refuses
  reconnects, the client is seen retrying, a second message is sent and
  stored with no socket open, and the proxy lets the socket back. No model
  call.
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.conversation-reconnect-callbacks/probe -- .venv/bin/python - <<'PY'
  import time
  import f33
  from openhands.sdk import Conversation
  from openhands.sdk.event import ConversationStateUpdateEvent, MessageEvent

  proxy = f33.Proxy()
  seen = []
  conv = Conversation(agent=f33.agent(), workspace=f33.workspace("gap", host=proxy.url), visualizer=None,
                      delete_on_close=False, callbacks=[seen.append])


  def updates():
      return sum(isinstance(e, ConversationStateUpdateEvent) for e in seen)


  def users():
      return [e for e in conv.state.events if isinstance(e, MessageEvent) and e.source == "user"]


  def wait(condition, seconds, why):
      deadline = time.monotonic() + seconds
      while not condition():
          assert time.monotonic() < deadline, why
          time.sleep(0.1)


  assert len(proxy.sockets) == 1, proxy.sockets
  conv.send_message("qa-f33 sent while connected")
  wait(lambda: any(isinstance(e, MessageEvent) and e.source == "user" for e in seen), 15, "the control message never reached the callback")
  proxy.block_ws = True
  proxy.drop_sockets()
  wait(lambda: proxy.refused >= 1, 30, "the client never tried to reconnect")
  before = updates()
  conv.send_message("qa-f33 sent while the socket reconnects")
  assert not proxy.sockets and updates() == before, (proxy.sockets, updates(), before)
  proxy.block_ws = False
  wait(lambda: updates() > before, 60, "the socket never came back")
  assert len(users()) == 2, users()
  missed = users()[1].id
  deadline = time.monotonic() + 15
  while missed not in {e.id for e in seen} and time.monotonic() < deadline:
      time.sleep(0.2)
  f33.emit("gap", cid=str(conv.id), message=missed, refused=proxy.refused, callback_got_message=missed in {e.id for e in seen})
  conv.close()
  PY
  GAP=$(jq -r .cid "$F33/gap.json")
  control-agent-server api GET "/api/conversations/$GAP/events/search" --query source=user --check items len-eq 2 \
    --check items.1.id eq "$(jq -r .message "$F33/gap.json")" --save F33.conversation-reconnect-callbacks/server-message
  test "$(jq .callback_got_message "$F33/gap.json")" = true  # bug
  ```
  The server stored both messages and, after the reconnect, `state.events`
  has both; the callback got the first one live, but today
  `callback_got_message` is `false` for the second (reproduced on every
  replay). A server restart opens the same gap: after one,
  `F33.conversation-reconnect`'s client also reconnects through `reconcile`
  only.
- **Reattach after a restart and continue (`F33.conversation-restart`).**
  ```sh
  control-agent-server restart
  control-agent-server exec --env PYTHONPATH="$F33" --env CID="$CID" --save F33.conversation-restart/probe -- .venv/bin/python - <<'PY'
  import json
  import os
  import uuid
  import f33
  from openhands.sdk import Conversation

  before = json.load(open(os.path.join(f33.DIR, "run.json")))["local"]
  conv = Conversation(agent=f33.agent(), workspace=f33.workspace("run"), conversation_id=uuid.UUID(os.environ["CID"]),
                      visualizer=None, delete_on_close=False)
  assert f33.kinds(conv.state.events) == before, (f33.kinds(conv.state.events), before)
  assert conv.state.execution_status.value == "finished"
  conv.send_message("Create the file qa-f33-after-restart.txt in the current directory containing exactly: again")
  conv.run(timeout=300)
  assert conv.state.execution_status.value == "finished", conv.state.execution_status
  f33.emit("restart", local=f33.kinds(conv.state.events))
  conv.close()
  PY
  test "$(cat "$F33/run/qa-f33-after-restart.txt")" = again
  control-agent-server api GET "/api/conversations/$CID" --check execution_status eq finished --save F33.conversation-restart/get
  test "$(jq .local.MessageEvent "$F33/restart.json")" -gt "$(jq .local.MessageEvent "$F33/run.json")"
  for K in MessageEvent ActionEvent ObservationEvent; do
    control-agent-server conversation events "$CID" --kinds $K --expect-count "$(jq ".local.$K" "$F33/restart.json")"
  done
  ```
  The history read after the restart equals the one recorded before it, and
  the continued conversation finishes and writes the second file.
- **A custom tool module (`F33.custom-tool-module`).** The documented
  custom-tool deployment seen from the SDK: the module `qa_f33_tool`
  registers `qa_f33_echo`, the server is restarted with
  `OH_EXTRA_PYTHON_PATH` naming the module's directory, and the probe
  imports the same module, so the SDK sends it in `tool_module_qualnames`
  without being told. The tool records in the workspace whether it ran in
  the agent server's process. A second module, `qa_f33_ghost`, sits where
  only the client can import it.
  ```sh
  mkdir -p "$F33/tools" "$F33/clientonly"
  cat > "$F33/tools/qa_f33_tool.py" <<'EOF'
  """qa-f33 custom tool: records where it ran in qa-f33-tool.txt in the workspace."""
  import os
  import sys

  from pydantic import Field

  from openhands.sdk import Action, Observation, ToolDefinition
  from openhands.sdk.tool import ToolExecutor, register_tool


  class QaF33EchoAction(Action):
      message: str = Field(description="Text to record")


  class QaF33EchoObservation(Observation):
      pass


  class QaF33EchoExecutor(ToolExecutor):
      def __init__(self, working_dir):
          self.working_dir = working_dir

      def __call__(self, action, conversation=None):
          where = "agent-server" if "openhands.agent_server" in sys.modules else "client"
          with open(os.path.join(self.working_dir, "qa-f33-tool.txt"), "w") as f:
              f.write(where)
          return QaF33EchoObservation.from_text(f"qa-f33 recorded {action.message!r} on the {where}")


  class QaF33EchoTool(ToolDefinition[QaF33EchoAction, QaF33EchoObservation]):
      name = "qa_f33_echo"

      @classmethod
      def create(cls, conv_state, **params):
          return [cls(description="Record a message (qa-f33 test tool).", action_type=QaF33EchoAction,
                      observation_type=QaF33EchoObservation, executor=QaF33EchoExecutor(conv_state.workspace.working_dir))]


  register_tool("qa_f33_echo", QaF33EchoTool)
  EOF
  sed -e 's/qa_f33_echo/qa_f33_ghost/g; s/QaF33Echo/QaF33Ghost/g' "$F33/tools/qa_f33_tool.py" > "$F33/clientonly/qa_f33_ghost.py"
  X0=$(control-agent-server logs --grep 'Extended sys.path with 1 directory' | jq -r .total_matching)
  control-agent-server restart --env OH_EXTRA_PYTHON_PATH="$F33/tools"
  control-agent-server logs --grep 'Extended sys.path with 1 directory' --expect-min "$((X0 + 1))"
  control-agent-server exec --env PYTHONPATH="$F33:$F33/tools" --save F33.custom-tool-module/probe -- .venv/bin/python - <<'PY'
  import f33
  import qa_f33_tool  # noqa: F401  registers qa_f33_echo in this process
  from openhands.sdk import Conversation, Tool
  from openhands.sdk.event import ObservationEvent
  from openhands.sdk.tool.registry import get_tool_module_qualnames

  assert get_tool_module_qualnames()["qa_f33_echo"] == "qa_f33_tool"
  agent = f33.agent()
  agent = agent.model_copy(update={"tools": [*agent.tools, Tool(name="qa_f33_echo")]})
  conv = Conversation(agent=agent, workspace=f33.workspace("customtool"), visualizer=None, delete_on_close=False)
  conv.send_message("Call the qa_f33_echo tool once with the message qa-f33-custom, then finish.")
  conv.run(timeout=300)
  assert conv.state.execution_status.value == "finished", conv.state.execution_status
  ran = [e for e in conv.state.events if isinstance(e, ObservationEvent) and e.tool_name == "qa_f33_echo"]
  assert ran and all("on the agent-server" in e.observation.text for e in ran), ran
  f33.emit("customtool", cid=str(conv.id), calls=len(ran))
  conv.close()
  PY
  TOOLCID=$(jq -r .cid "$F33/customtool.json")
  test "$(cat "$F33/customtool/qa-f33-tool.txt")" = agent-server
  control-agent-server api GET "/api/conversations/$TOOLCID" --check execution_status eq finished \
    --check tool_module_qualnames.qa_f33_echo eq qa_f33_tool --check tool_module_qualnames.terminal exists \
    --check agent.tools contains qa_f33_echo --save F33.custom-tool-module/get
  control-agent-server conversation events "$TOOLCID" --kinds ObservationEvent --contains '"kind": "QaF33EchoObservation"' \
    --expect-count "$(jq .calls "$F33/customtool.json")" --save F33.custom-tool-module/observations
  control-agent-server exec --env PYTHONPATH="$F33" --env CID="$TOOLCID" --save F33.custom-tool-module/reattach -- .venv/bin/python - <<'PY'
  import os
  import uuid
  import f33
  from openhands.sdk import Conversation, RemoteConversation

  cid = uuid.UUID(os.environ["CID"])
  errors = []
  for attach in (lambda: RemoteConversation.attach(f33.workspace("customtool"), cid, visualizer=None),
                 lambda: Conversation(agent=f33.agent(), workspace=f33.workspace("customtool"), conversation_id=cid,
                                      visualizer=None, delete_on_close=False)):
      try:
          attach().close()
          raise AssertionError("attached without the tool module")
      except ImportError as e:
          errors.append(str(e))
  assert all("'qa_f33_echo'" in e and "'qa_f33_tool'" in e for e in errors), errors
  f33.emit("customtool-reattach", errors=errors)
  PY
  W0=$(control-agent-server logs --grep 'Failed to import' | jq -r .total_matching)
  control-agent-server exec --env PYTHONPATH="$F33:$F33/tools:$F33/clientonly" --save F33.custom-tool-module/unused-ghost -- .venv/bin/python - <<'PY'
  import f33
  import qa_f33_ghost  # noqa: F401  registered here, not importable by the server
  from openhands.sdk import Conversation

  conv = Conversation(agent=f33.agent(), workspace=f33.workspace("ghostunused"), visualizer=None, delete_on_close=False)
  conv.send_message("Reply with one word: ready")
  conv.run(timeout=300)
  assert conv.state.execution_status.value == "finished", conv.state.execution_status
  f33.emit("ghostunused", cid=str(conv.id))
  conv.close()
  PY
  control-agent-server logs --grep 'Failed to import' --expect-min "$((W0 + 1))"
  control-agent-server api GET "/api/conversations/$(jq -r .cid "$F33/ghostunused.json")" --check execution_status eq finished \
    --check tool_module_qualnames.qa_f33_ghost eq qa_f33_ghost --save F33.custom-tool-module/unused-ghost-get
  control-agent-server api GET /api/tools/ --check . contains qa_f33_echo --check . not-contains qa_f33_ghost
  ```
  The SDK sends every tool registered in its process, so `qa_f33_echo`
  (with `terminal` and the rest) reaches `tool_module_qualnames`; the
  server imports the module from `OH_EXTRA_PYTHON_PATH`, the agent's call
  runs in the server process (the file says `agent-server`) and returns a
  `QaF33EchoObservation`. A process without the module cannot reattach:
  both `attach()` and `Conversation(conversation_id=...)` raise
  `ImportError` naming the tool and the module. A client-registered module
  the server cannot import is only a logged `Failed to import module`
  warning while the agent does not use its tool: that conversation runs
  normally and the server's registry never lists `qa_f33_ghost`.
- **An agent tool the server cannot import (`F33.custom-tool-module-unimportable`), known bug.**
  Requires: `F33.custom-tool-module`
  The agent now names `qa_f33_ghost`. The create only logs the import
  warning and answers 201 (`conversation_service.py`: "The agent will fail
  gracefully if it tries to use unregistered tools"); the agent is built on
  the first message, where `resolve_tool` raises `KeyError: "ToolDefinition
  'qa_f33_ghost' is not registered"` and `POST .../events` answers an
  unhandled 500, as does every later message. The correct behavior asserted
  here is a client error that the caller can act on (a 4xx at create or on
  the message, or a recorded conversation error), never a 500.
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33:$F33/clientonly" --save F33.custom-tool-module-unimportable/probe -- .venv/bin/python - <<'PY'
  import httpx
  import f33
  import qa_f33_ghost  # noqa: F401
  from openhands.sdk import Conversation, Tool

  agent = f33.agent()
  agent = agent.model_copy(update={"tools": [*agent.tools, Tool(name="qa_f33_ghost")]})
  statuses, cid = {}, None
  try:
      conv = Conversation(agent=agent, workspace=f33.workspace("ghost"), visualizer=None, delete_on_close=False)
      statuses["create"], cid = 201, str(conv.id)
      try:
          conv.send_message("Reply with one word: ready")
          statuses["message"] = 200
      except httpx.HTTPStatusError as e:
          statuses["message"] = e.response.status_code
      conv.close()
  except httpx.HTTPStatusError as e:
      statuses["create"] = e.response.status_code
  f33.emit("ghost", cid=cid, statuses=statuses, server_errors=sum(s >= 500 for s in statuses.values()))
  PY
  control-agent-server api GET /api/tools/ --check . contains terminal --check . not-contains qa_f33_ghost
  test "$(jq .server_errors "$F33/ghost.json")" -eq 0  # bug
  ```
  The server's registry lacks `qa_f33_ghost` (control). Today the probe
  records `{"create": 201, "message": 500}`; the server log has the
  `KeyError` traceback for the message `POST`.
- **Close keeps or deletes (`F33.conversation-close`).**
  ```sh
  control-agent-server exec --env PYTHONPATH="$F33" --save F33.conversation-close/probe -- .venv/bin/python - <<'PY'
  import f33
  from openhands.sdk import Conversation

  ws = f33.workspace("close")
  keep = Conversation(agent=f33.agent(), workspace=ws, visualizer=None, delete_on_close=False)
  gone = Conversation(agent=f33.agent(), workspace=ws, visualizer=None)
  assert (keep.delete_on_close, gone.delete_on_close) == (False, True)
  keep.close()
  gone.close()
  f33.emit("close", keep=str(keep.id), gone=str(gone.id))
  PY
  control-agent-server api GET "/api/conversations/$(jq -r .keep "$F33/close.json")" --expect 200 --save F33.conversation-close/kept
  control-agent-server api GET "/api/conversations/$(jq -r .gone "$F33/close.json")" --expect 404 --save F33.conversation-close/deleted
  ```
  The conversation closed with `delete_on_close=False` is still served; the
  factory default deleted the other one.

## Gotchas

- `exec` reports the probe's stdout in `stdout_tail` and the full transcript
  under `evidence/<ID>/probe.json`; the SDK logs (and, without
  `visualizer=None`, rich panels on stdout) are noisy, so probes pass
  `visualizer=None` and the preconditions export `OPENHANDS_SUPPRESS_BANNER=1`.
  Importing the SDK takes about 8 s per probe.
- The `Conversation(...)` factory defaults to `delete_on_close=True`, the
  `RemoteConversation` class to `False`: a program that calls `close()` on a
  factory-made conversation deletes it on the server, so a program whose
  conversation another program will reattach must pass
  `delete_on_close=False`. `close()` also runs from `__del__`, but not
  reliably at interpreter exit.
- `RemoteWorkspace.execute_command` without `cwd` sends no `cwd`: the host
  bash service then uses the server process's directory (the run's `server/`),
  not `working_dir` (a runtime-scoped workspace uses the conversation's
  workspace). `LocalWorkspace` defaults to `working_dir`; the remote omission
  is deliberate (`tests/sdk/workspace/remote/test_remote_workspace_mixin.py`),
  so pass `cwd` explicitly.
- `execute_command`, `file_upload` and `file_download` never raise on HTTP
  errors: they return exit `-1` with `Remote execution error: ...` in stderr,
  or `success: false` with the httpx message in `error`. Check the result.
  `git_changes`, `git_diff`, `get_llm` and the conversation methods do raise.
- On a command timeout the SDK stops polling and returns exit `-1` with
  `Command timed out after N seconds` and empty stdout, even though the
  server recorded the output printed before its own timeout.
- `file_upload` reports `file_size: null` (the server answers only
  `{"success": true}`), and its destination must be absolute (a relative one
  is a 400), while `git_changes`/`git_diff` join relative paths with
  `working_dir`.
- The server persists per-field `ConversationStateUpdateEvent`s
  (`execution_status`, `stats`, ...), but the full-state snapshots the socket
  sends on connect and after each run are never persisted, so
  `len(conversation.state.events)` is not the server's event count; compare
  kinds other than state updates, as the recipes do. Count kinds with
  `conversation events --kinds`, not `events/count?kind=ActionEvent`: the
  server only matches the full `<module>.<Class>` name there (the
  `F06.search-kind-short` known bug), so a short name always counts `0`.
- A live `RemoteConversation` reconnects its events socket with exponential
  backoff (1 s doubling to 30 s) after a server restart or a dropped
  connection; events emitted before it reconnects reach `state.events`
  through REST reconciliation on reconnect and after `run()`, but registered
  callbacks never see them (`F33.conversation-reconnect-callbacks`, known
  bug; the server's `resend_mode=since` replay exists but the client does
  not use it). Consumers that stream through callbacks should re-read
  `state.events` after a reconnect. How much a run loses depends on the
  backoff timing, so the reconnect bullet asserts only `state.events`, and
  the known-bug bullet makes the outage exact with `f33.Proxy` instead of a
  restart (a restart's window between "server ready" and the next retry
  varies from under a second to many seconds).
- `run()` posts `/run` with a 30 s timeout. While the agent's tool call is
  executing, the server's `/run` (and `/pause`) wait for the conversation's
  state lock, so `run()` on a conversation that is already running a command
  longer than 30 s raises `ConversationRunError` (`ReadTimeout`) although
  the run is healthy (`F33.conversation-run-join-long-tool`, known bug); a
  shorter command is joined normally (`F33.conversation-run-timeout`).
- `RemoteConversation.create(...)` with an `initial_message` starts a run on
  the server whatever `initial_message.run` says (see F04); repeating the
  request with the same `conversation_id` returns the existing conversation
  without sending the message again.
- `RemoteWorkspace(runtime_conversation_id=...).get_runtime_session_key()`
  and `release_runtime()` call `POST .../runtime/credentials` and
  `DELETE .../runtime`, which only a Docker conversation runtime serves; a
  local server answers 404 and 405, and the SDK raises on both (it treats
  only 404 from `release_runtime` as already released).
- `RemoteConversation.attach()` rebuilds the agent from the server, whose LLM
  key comes back redacted: use it to read or steer, and use
  `Conversation(agent=<your agent>, conversation_id=...)` when the client
  must call the model itself (`generate_title()` runs on the client with the
  agent's LLM unless given another).
- `run()` treats only the server's post-run full-state socket snapshot as
  completion; without it, it accepts a REST-confirmed terminal status after
  30 s, so a broken socket makes every run at least 30 s long
  (`F33.ws-unavailable`). `ERROR` and `STUCK` raise `ConversationRunError`
  at once (`F33.conversation-run-error`, `F33.conversation-stuck`).
- With the socket required (the default), `Conversation(...)` creates the
  server conversation before it waits for the socket, so a
  `WebSocketConnectionError` leaves an `idle` conversation behind; its id is
  on the exception (`conversation_id`).
- The server's auto-title runs on the agent's own LLM (unless the create
  names `title_llm_profile`) for the first user message of a conversation
  without a title. With an `llm-stub` agent it takes a scripted step,
  racing the agent's first call; with `log_completions` it logs one more
  call, often after `run()` returned; its cost lands in the agent's
  `usage_to_metrics`. `RemoteConversation` sends no `autotitle` field, so
  call `set_title()` before the first message to keep a stub's script and
  the call count to the agent's own.
- `tool_module_qualnames` is every tool registered in the client process at
  create time, not only the agent's tools, and a later attach imports all of
  them: a program that cannot import one of those modules cannot attach,
  even if the agent never used that tool (`ImportError` names the first
  missing one). A client-registered module the server cannot import is only
  a warning, until an agent names its tool (then every message is a 500,
  `F33.custom-tool-module-unimportable`). `OH_EXTRA_PYTHON_PATH` only
  extends the server's `sys.path`; F27 drives the server side
  (`F27.custom-tool-modules`, and `F27.custom-tool-missing-on-resume` for a
  module that disappears).
- Client tools have no server executor: the server acknowledges each call
  with `Tool call dispatched to client.` at once, and the agent continues
  without waiting for the client's handler. The client learns of the call
  only through callbacks, so a client tool called while the socket is down
  is missed by the handler (the reconnect gap above).
- `RemoteState`'s polymorphic readers (`confirmation_policy`,
  `security_analyzer`, `agent`) validate the cached conversation info in
  place, so only the first read after a refresh is right
  (`F33.conversation-state-reread`, known bug); call
  `state.refresh_from_server()` before each read, or read once and keep the
  value. `RemoteConversation.agent` (the attribute) is unaffected.
- With a `stream=True` LLM the events socket also carries
  `StreamingDeltaEvent` frames: callbacks get them, the server stores none,
  but `state.events` keeps them (`F33.conversation-streaming-state`, known
  bug), so `len(state.events)` and kind counts include one entry per delta;
  `token_callbacks` passed to `Conversation(...)` are ignored for a remote
  workspace (`F33.conversation-token-callbacks`, known bug).
- `log_completions=True` on a remote agent writes the log files on the
  client (relative folders resolve against the client's working directory),
  from `LLMCompletionLogEvent`s that the server also stores and serves like
  any event.
- `fork()` returns a `RemoteConversation` built with the default visualizer
  and no callbacks, whatever the source used: rich panels go to stdout for
  the fork's events unless the program passes its own fork to a quieter
  `Conversation(conversation_id=...)`.
- `PluginSource` masks inline URL credentials on every dump, including the
  create request, so the server receives `https://****@host/...`: plugins
  that need credentials cannot be fetched by a remote server from an inline
  token (only the client-side `source` attribute keeps it).
- Models refuse to print or copy a secret into a file; prove secret delivery
  with a hash prefix computed in the shell, as the secrets bullet does.
