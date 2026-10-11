# Hooks, sub-agents and tool catalogs

Read-only discovery routes a client calls before it configures or starts an
agent. `POST /api/hooks` reads a workspace's `.openhands/hooks.json` and
returns it as a normalized `HookConfig` (or `null`), which the client then
passes as `hook_config` when it creates a conversation; only then do the hooks
run and emit `HookExecutionEvent`s. `POST /api/sub-agents` lists the delegate
agents a workspace offers (project, user and built-in Markdown definitions,
first wins by name). `GET /api/tools/` lists the names in the process-wide
tool registry, and `GET /api/tools/catalog` lists the tools a client may offer
when configuring an agent, with selectability, usability on this server and
default-set membership. None of them changes state. The family also covers
what a client registers through the create request and the server's startup
flags: hooks that block a tool call or a prompt, client-side tools
(`client_tools`) whose calls the client executes, forwarded sub-agent
definitions (`agent_definitions`), and custom Python tool modules
(`tool_module_qualnames`, `OH_EXTRA_PYTHON_PATH`, `--import-modules`).

Source: `openhands-agent-server/openhands/agent_server/tool_router.py`, `openhands-agent-server/openhands/agent_server/hooks_router.py`, `openhands-agent-server/openhands/agent_server/hooks_service.py`, `openhands-agent-server/openhands/agent_server/sub_agents_router.py`, `openhands-agent-server/openhands/agent_server/launch.py`, `openhands-agent-server/openhands/agent_server/__main__.py`, `openhands-sdk/openhands/sdk/tool/registry.py`, `openhands-sdk/openhands/sdk/tool/client_tool.py`, `openhands-sdk/openhands/sdk/hooks/`, `openhands-sdk/openhands/sdk/subagent/`, `openhands-tools/openhands/tools/preset/`, `openhands-tools/openhands/tools/task/`, `openhands-tools/openhands/tools/browser_use/`, `clients/typescript/src/client/tool-client.ts`, `clients/typescript/src/client/hooks-client.ts`, `clients/typescript/src/client/sub-agents-client.ts`

Needs: `llm`, `git`, `tmux`, `chromium`

Routes: `GET /api/tools/`, `GET /api/tools/catalog`, `POST /api/sub-agents`,
`POST /api/hooks`

## Sub-features

- `F27.auth`: every route answers 401 without a session key or with a wrong one; the slash redirect of `/api/tools` answers before authentication but its target still needs the key.
- `F27.validation`: a missing body or a wrongly typed field is 422 on `POST /api/hooks` and `POST /api/sub-agents`.
- `F27.tools-list`: `GET /api/tools/` lists the registered tool names, including the default tools, `browser_tool_set`, the task and planning tools and registrations under a class name (`ClassifyAndSwitchLLMTool`).
- `F27.tools-redirect`: `GET /api/tools` (no trailing slash) answers 307 to `/api/tools/`, and `/api/tools/catalog/` answers 307 to `/api/tools/catalog`; the CLI does not follow redirects.
- `F27.catalog`: `GET /api/tools/catalog` describes each offered tool with `user_selectable`, `usable`, `description` and `in_default_set`: the default tools are selectable default-set entries, internal tools are not selectable, and built-ins appear under their tool names.
- `F27.hooks-project`: the project fixture's `hooks.json` (legacy `{"hooks": {...}}` wrapper with PascalCase keys) comes back as snake_case `HookConfig` with defaults filled in.
- `F27.hooks-formats`: a snake_case `hooks.json` keeps its matcher, timeout and `async` flag, a PascalCase key next to it is normalized, a `prompt` hook keeps its prompt, and a matcher left out becomes `*`.
- `F27.hooks-absent`: no `project_dir`, a misspelled field (`projectDir`), a missing directory, a directory without `.openhands/hooks.json`, a `hooks.json` only under `.agents/`, and an empty `{}` file all return 200 with `hook_config: null`.
- `F27.hooks-invalid`: unparsable JSON, an unknown event (PascalCase or snake_case), both spellings of one event, and a command hook without a command return 200 with `hook_config: null` and log `Failed to load hooks`.
- `F27.hooks-conversation`: the config `POST /api/hooks` returns, passed as `hook_config` to a DeepSeek conversation in that project, runs: a `HookExecutionEvent` with the hook's stdout appears over REST and the WebSocket replay, and the conversation keeps its `hook_config` across a restart.
- `F27.hooks-not-automatic`: a conversation in the same project started without `hook_config` runs no hook, although the project has `hooks.json` and the agent used the terminal.
- `F27.hooks-block`: a `PreToolUse` hook that exits 2 stops the agent's terminal call (a `HookExecutionEvent` with `blocked: true`, a `UserRejectObservation` from the hook with its stderr as the reason, no terminal observation, no file) and records the action in `blocked_actions` until the agent consumes it; a `UserPromptSubmit` hook that exits 2 records the queued message in `blocked_messages`, and running finishes at once without a model call.
- `F27.subagents-builtin`: without project and user agents (switched off, or a `project_dir` that does not exist) the catalog is exactly the four built-ins (`bash-runner`, `code-explorer`, `general-purpose`, `web-researcher`) with `level: builtin` and `is_builtin: true`, `mcp_servers` mirrors `mcp_config`, and `load_builtin: false` empties it.
- `F27.subagents-project`: project agents from `.agents/agents` and `.openhands/agents` are listed with `level: project`, their `source` file, and every frontmatter field (tools, skills, color, permission mode, iteration limit, `<example>` triggers, unknown keys in `metadata`, the body as `system_prompt`).
- `F27.subagents-user`: user agents from `~/.agents/agents` and `$OH_PERSISTENCE_DIR/agents` are listed with `level: user` only while `load_user` is on, and `load_project: false` hides the project's agents.
- `F27.subagents-precedence`: names are unique and the first source wins: a project agent shadows the built-in and the user agent of the same name, and `.agents/agents` shadows `.openhands/agents`.
- `F27.subagents-invalid`: definitions that do not validate (bad `permission_mode`, `max_iteration_per_run: 0`) are skipped with a logged warning, and `README.md`, subdirectories and non-`.md` files are ignored, while the request still answers 200.
- `F27.subagents-bad-skill`: a project agent whose `skills` names a skill that does not exist should not break conversations in that workspace; today the catalog lists it as valid and a message to such a conversation is a 500.
- `F27.catalog-sealed`: a client tool registered by a conversation (`client_tools`) is part of that conversation's agent but never enters the catalog, which is sealed at startup.
- `F27.client-tool-call`: when the agent calls a client tool, the events socket delivers a `ClientAction_<name>` `ActionEvent` with the model's arguments, the server answers it with the `Tool call dispatched to client.` acknowledgment and the run finishes; the tool is registered again when the conversation resumes after a restart; a duplicate name in one request, a name that collides with `terminal` and a reused name with a different schema are each 422 and create nothing, while the same name with the same schema is accepted.
- `F27.agent-definitions`: `agent_definitions` in the create request make that sub-agent available to the conversation's task tool, which delegates to it by name (the delegate runs the command, the `TaskObservation` names it); the definition is stored with the conversation and is not listed by `POST /api/sub-agents`.
- `F27.custom-tool-modules`: with `OH_EXTRA_PYTHON_PATH` naming a directory, `tool_module_qualnames` imports a module from it at create, the agent runs its tool, the conversation reports the mapping and `GET /api/tools/` lists the tool (the sealed catalog does not); after a restart the tool is gone until the conversation is loaded again, and a new message runs it again; an unimportable module is a logged warning and the create still succeeds.
- `F27.import-modules`: a server started with `--import-modules` (and `OH_EXTRA_PYTHON_PATH`) lists the module's tool in `GET /api/tools/` and offers it in the catalog before any conversation exists; a module that cannot be imported stops the server before it binds its port.
- `F27.custom-tool-missing-on-resume`: when a conversation's tool module can no longer be imported after a restart, the server should drop the tool with its warning and keep the conversation readable; today its event search and a new message are 500 (`Unknown kind`).
- `F27.browser-playwright-path`: the browser probe should find a Playwright Chromium in `$PLAYWRIGHT_BROWSERS_PATH`; today it only looks in standard paths, `~/.cache/ms-playwright` and `PATH`, and reports `browser_tool_set` unusable.
- `F27.browser-probe`: without a Chromium the server can find, the catalog reports `browser_tool_set` unusable and `/server_info` `usable_tools` leaves it out; with a Playwright Chromium in the server's `~/.cache/ms-playwright` and a restart, the catalog reports it usable and `usable_tools` lists it.
- `F27.browser-disabled-launch`: a conversation started from the saved settings (default tools) gets `browser_tool_set` while the server finds Chromium, and leaves it out after a restart with `enable_browser: false`; a conversation created before keeps its stored tools.
- `F27.browser-disabled`: with `enable_browser: false` the catalog reports `browser_tool_set` unusable although Chromium is present, while `GET /api/tools/` still lists it.
- `F27.server-info-browser-disabled`: with `enable_browser: false`, `/server_info` `usable_tools` should agree with the catalog and leave out `browser_tool_set`; today it still lists it.
- `F27.subagents-browser-disabled`: with `enable_browser: false`, the sub-agents catalog should leave out the browser-only `web-researcher`; today it still lists it.
- `F27.catalog-docker-runtime`: with `conversation_runtime: docker` the catalog probes nothing in the server process: `browser_tool_set` is usable for the stock image although the server finds no Chromium, and unusable for a `-minimal` image tag or with `conversation_image_has_browser: false`.

## How to get to it (agent POV)

- REST, hooks: `POST /api/hooks` with `{"project_dir": "<abs path>"}`
  (`HooksRequest`) returns `{"hook_config": HookConfig | null}`. Only
  `<project_dir>/.openhands/hooks.json` is read. Driven by the `F27.hooks-*`
  bullets.
- REST, conversations (second view, owned by the conversation family):
  `StartConversationRequest.hook_config` carries the returned config into
  `POST /api/conversations`; `GET /api/conversations/{id}` echoes
  `hook_config`, and `GET /api/conversations/{id}/events/search` and the
  WebSocket below carry `HookExecutionEvent` (`hook_event_type`,
  `hook_command`, `stdout`, `exit_code`, `blocked`, `action_id`). The server
  never loads a project's `hooks.json` into a conversation on its own.
  Driven by `F27.hooks-conversation` and `F27.hooks-not-automatic`. A hook
  that exits 2 blocks: `PreToolUse` turns the call into a
  `UserRejectObservation` (`rejection_source: hook`), `UserPromptSubmit`
  makes the next run finish without a model call; the pending entries are
  `blocked_actions` and `blocked_messages` on `GET /api/conversations/{id}`
  and in `ConversationStateUpdateEvent`s (`F27.hooks-block`).
- REST, create-time registrations (second view, the route is owned by the
  conversation family): `StartConversationRequest.client_tools`
  (`ClientToolSpec`: `name`, `description`, object `parameters`,
  `annotations`), `agent_definitions` (`AgentDefinition` list, the same shape
  `POST /api/sub-agents` returns) and `tool_module_qualnames` (tool name to
  module path, imported at create and again on resume).
  `GET /api/conversations/{id}` echoes `client_tools` and
  `tool_module_qualnames`; `agent_definitions` is kept in `meta.json` only.
  A registration error is `422` with the reason in `detail`. Driven by
  `F27.client-tool-call`, `F27.agent-definitions` and
  `F27.custom-tool-modules`.
- REST, sub-agents: `POST /api/sub-agents` with `SubAgentsRequest`
  (`load_user`, `load_project`, `load_builtin`, all default `true`, and
  `project_dir`) returns `{"agents": [SubAgentInfo]}` with every
  `AgentDefinition` field plus `level`, `source` and `is_builtin`. Driven by
  the `F27.subagents-*` bullets.
- REST, tools: `GET /api/tools/` returns a plain JSON array of registered
  names; `GET /api/tools/catalog` returns `{"tools": [ToolCatalogEntry]}`.
  `/server_info` `usable_tools` (owned by the server status family) is the
  older usability view; the tool bullets compare it with the catalog.
- WebSocket: `/sockets/events/{conversation_id}` delivers the
  `HookExecutionEvent` live and on replay (`resend_mode=all`); owned by the
  events family, replayed by `F27.hooks-conversation`. It is also how a
  client learns that the agent called one of its client tools: an
  `ActionEvent` whose action `kind` is `ClientAction_<name>`
  (`F27.client-tool-call`).
- Server process: `OH_EXTRA_PYTHON_PATH` (or `--extra-python-path`) adds
  directories to `sys.path`; `--import-modules a,b` imports modules at
  startup, before the catalog is sealed, and a failed import is fatal. The
  documented custom-tool image sets `OH_EXTRA_PYTHON_PATH`
  (`examples/02_remote_agent_server/06_custom_tool/Dockerfile`).
  `control-agent-server launch` and `restart` pass environment variables but
  no server arguments, so `F27.import-modules` starts that server in plain
  shell and attaches to it.
- Configuration: `enable_browser` (config file, or `OH_ENABLE_BROWSER`) forces
  the catalog's browser entry to unusable and drops the browser from the
  default tools of conversations created afterwards
  (`F27.browser-disabled-launch`); with `conversation_runtime: docker`
  nothing is probed and browser usability comes from `conversation_image` and
  `conversation_image_has_browser` (`F27.catalog-docker-runtime`, which
  needs no Docker daemon because no conversation starts). The browser probe
  reads the server process's `HOME`.
- TypeScript client: `HooksClient.loadHooks()` and
  `RemoteConversation.loadHooks(projectDir)` (defaults to the workspace's
  working directory), `SubAgentsClient.getSubAgents()` (also on
  `ConversationManager.subAgents` and `OpenHandsClient.subAgents`),
  `ToolClient.listTools()` and `ToolClient.getToolCatalog()`. The Python SDK
  has no method for these routes; `RemoteConversation` forwards its process's
  `tool_module_qualnames` and registered agent definitions on create. The
  TypeScript consumer lane, with its build, runs in the TypeScript client
  family; `HooksClient` is driven there.
- Agent Canvas (context only): the conversation start flow loads hooks for the
  workspace, the sub-agent picker reads the sub-agents catalog, and the
  agent-profile tool picker reads the tool catalog.

## Driving it with control-agent-server

Preconditions:

- A run is live and exported, `doctor` is ok, `$DEEPSEEK_API_KEY` is set,
  `git` and `tmux` are installed, and `control-agent-server capabilities`
  reports a Chromium binary (`machine.chromium`) that the server cannot find
  on its own: not at a standard path (`/usr/bin/chromium`,
  `/usr/bin/google-chrome`, ...) and not on `PATH`, for example a Playwright
  install under `$PLAYWRIGHT_BROWSERS_PATH` (`launch` does not pass that
  variable on). Otherwise the browser bullets cannot show the unusable state
  (see Gotchas). No launch flags are needed. Several bullets restart the run
  (`F27.hooks-conversation`, `F27.client-tool-call`, the custom-tool
  bullets with `--env OH_EXTRA_PYTHON_PATH`, the browser bullets with
  `--env` and `--config-json`), and a restart empties the process-wide tool
  and sub-agent registries; the browser bullets come last.
  `F27.import-modules` also starts and stops a second server of this
  checkout in plain shell, on a free loopback port.
- The block below activates `deepseek-flash`, creates the `qa-f27-proj`
  project fixture (its `.agents/agents/qa-helper.md`, its
  `.openhands/hooks.json` with a `PreToolUse` hook `echo QA_PROJECT_HOOK`,
  and its skill `qa-f27-proj-skill`), adds more project agents, user agents
  in the run's `HOME`, `hooks.json` variants under `fixtures/qa-f27/`, a
  `badskill` project whose agent names a missing skill, a run-owned
  Playwright browsers directory that links the machine's Chromium binary,
  and `pytools/qa_f27_tools.py`: a custom tool module that registers the
  tool `qa_f27_echo` on import (it answers `QA_F27_ECHO_RAN <marker>`).
  ```sh
  control-agent-server llm preset deepseek
  P=$(control-agent-server fixture project --name qa-f27-proj --print-path)
  F="$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f27"
  H="$AGENT_SERVER_VERIFY_RUN/home"
  mkdir -p "$F/snake/.openhands" "$F/empty/.openhands" "$F/badjson/.openhands" "$F/unknown/.openhands" \
    "$F/snakeunknown/.openhands" "$F/both/.openhands" "$F/nocmd/.openhands" "$F/agentsdir/.agents" "$F/nohooks" \
    "$F/badskill/.agents/agents" "$P/.openhands/agents" "$P/.agents/agents/qa-nested-dir" "$H/.agents/agents" "$H/.openhands/agents"
  printf '%s\n' '{"pre_tool_use": [{"matcher": "terminal|file_editor", "hooks": [{"command": "echo QA_SNAKE", "timeout": 7, "async": true}]}], "Stop": [{"hooks": [{"type": "prompt", "prompt": "QA done?"}]}]}' > "$F/snake/.openhands/hooks.json"
  printf '%s\n' '{}' > "$F/empty/.openhands/hooks.json"
  printf '%s\n' '{bad' > "$F/badjson/.openhands/hooks.json"
  printf '%s\n' '{"PreToolUsage": [{"hooks": [{"command": "true"}]}]}' > "$F/unknown/.openhands/hooks.json"
  printf '%s\n' '{"pre_tool_usage": [{"hooks": [{"command": "true"}]}]}' > "$F/snakeunknown/.openhands/hooks.json"
  printf '%s\n' '{"PreToolUse": [{"hooks": [{"command": "true"}]}], "pre_tool_use": []}' > "$F/both/.openhands/hooks.json"
  printf '%s\n' '{"pre_tool_use": [{"hooks": [{"type": "command"}]}]}' > "$F/nocmd/.openhands/hooks.json"
  printf '%s\n' '{"pre_tool_use": [{"hooks": [{"command": "true"}]}]}' > "$F/agentsdir/.agents/hooks.json"
  printf -- '---\nname: qa-reviewer\ndescription: QA reviewer. <example>review the qa fixture</example>\ncolor: purple\ntools:\n  - terminal\n  - file_editor\nskills: qa-f27-proj-skill\npermission_mode: never_confirm\nmax_iteration_per_run: 5\nqa_extra: QA_META\n---\nYou are the QA reviewer. Say QA_REVIEWER_MARKER.\n' > "$P/.agents/agents/qa-reviewer.md"
  printf -- '---\nname: general-purpose\ndescription: QA override of a built-in\n---\nQA_OVERRIDE_BODY\n' > "$P/.agents/agents/general-purpose.md"
  printf -- '---\nname: qa-bad-mode\npermission_mode: sometimes\n---\nQA_BAD\n' > "$P/.agents/agents/qa-bad-mode.md"
  printf -- '---\nname: qa-bad-iter\nmax_iteration_per_run: 0\n---\nQA_BAD\n' > "$P/.agents/agents/qa-bad-iter.md"
  printf -- '---\nname: qa-readme\n---\nQA_README\n' > "$P/.agents/agents/README.md"
  printf -- '---\nname: qa-nested\n---\nQA_NESTED\n' > "$P/.agents/agents/qa-nested-dir/qa-nested.md"
  printf 'name: qa-notes\n' > "$P/.agents/agents/qa-notes.txt"
  printf -- '---\ndescription: QA legacy project agent\n---\nQA_LEGACY_AGENT\n' > "$P/.openhands/agents/qa-legacy-agent.md"
  printf -- '---\nname: qa-reviewer\ndescription: QA shadowed copy\n---\nQA_SHADOWED\n' > "$P/.openhands/agents/qa-reviewer.md"
  printf -- '---\nname: qa-helper\ndescription: QA user copy of qa-helper\n---\nQA_USER_COPY\n' > "$H/.agents/agents/qa-helper.md"
  printf -- '---\nname: qa-user-agent\ndescription: QA user agent\n---\nQA_USER_AGENT\n' > "$H/.agents/agents/qa-user-agent.md"
  printf -- '---\nname: qa-persist-agent\ndescription: QA persistence-dir agent\n---\nQA_PERSIST_AGENT\n' > "$H/.openhands/agents/qa-persist-agent.md"
  printf -- '---\nname: qa-bad-skill\ndescription: QA agent naming a missing skill\nskills: qa-f27-missing-skill\n---\nQA_BAD_SKILL\n' > "$F/badskill/.agents/agents/qa-bad-skill.md"
  CHROME=$(control-agent-server capabilities | jq -r '.machine.chromium.detail')
  PW="$F/playwright"
  mkdir -p "$PW/chromium-qa/chrome-linux"
  ln -sfn "$(readlink -f "$CHROME")" "$PW/chromium-qa/chrome-linux/chrome"
  test -x "$PW/chromium-qa/chrome-linux/chrome"
  mkdir -p "$F/pytools"
  cat > "$F/pytools/qa_f27_tools.py" <<'EOF'
  from pydantic import Field

  from openhands.sdk import Action, Observation, ToolDefinition
  from openhands.sdk.tool import ToolExecutor, register_tool


  class QaF27EchoAction(Action):
      marker: str = Field(description="The marker text to echo back.")


  class QaF27EchoObservation(Observation):
      pass


  class QaF27EchoExecutor(ToolExecutor):
      def __call__(self, action, conversation=None):
          return QaF27EchoObservation.from_text(text=f"QA_F27_ECHO_RAN {action.marker}")


  class QaF27EchoTool(ToolDefinition[QaF27EchoAction, QaF27EchoObservation]):
      @classmethod
      def create(cls, conv_state=None, **params):
          return [cls(description="QA echo tool: returns the marker it is given.", action_type=QaF27EchoAction,
                      observation_type=QaF27EchoObservation, executor=QaF27EchoExecutor())]


  register_tool("qa_f27_echo", QaF27EchoTool)
  EOF
  .venv/bin/python -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import qa_f27_tools' "$F/pytools"
  ```

- **Session key required (`F27.auth`).** Call every route without a key and
  with a wrong one, then the list with the run's key as the positive control.
  ```sh
  control-agent-server api GET /api/tools/ --auth none --expect 401 --save F27.auth/tools-no-key
  control-agent-server api GET /api/tools/ --auth bad --expect 401
  control-agent-server api GET /api/tools/catalog --auth none --expect 401
  control-agent-server api POST /api/sub-agents --auth none --json '{}' --expect 401
  control-agent-server api POST /api/hooks --auth none --json '{}' --expect 401
  control-agent-server api GET /api/tools/catalog --auth bad --expect 401
  control-agent-server api POST /api/sub-agents --auth bad --json '{}' --expect 401
  control-agent-server api POST /api/hooks --auth bad --json '{}' --expect 401
  NOSLASH=/api/tools
  control-agent-server api GET "$NOSLASH" --auth none --expect 307 --check-header location matches '/api/tools/$'
  control-agent-server api GET /api/tools/ --expect 200 --check . contains terminal
  ```
  Every call without a valid key is `401`; with the key the list is `200`.
  The slash redirect answers `307` without a key (it reveals nothing), and its
  target is the `401` above.
- **Bad bodies (`F27.validation`).** Send no body and wrong types.
  ```sh
  control-agent-server api POST /api/hooks --expect 422 --check detail.0.loc.0 eq body --save F27.validation/hooks-no-body
  control-agent-server api POST /api/hooks --json '{"project_dir": 123}' --expect 422 --check detail.0.loc.1 eq project_dir
  control-agent-server api POST /api/sub-agents --expect 422 --check detail.0.loc.0 eq body
  control-agent-server api POST /api/sub-agents --json '{"load_user": "maybe"}' --expect 422 --check detail.0.loc.1 eq load_user \
    --save F27.validation/sub-agents-bad-bool
  control-agent-server api POST /api/sub-agents --json '{"project_dir": ["x"]}' --expect 422 --check detail.0.loc.1 eq project_dir
  ```
  Each answer is `422` and names the offending location.
- **Registered tools (`F27.tools-list`).** Read the raw registry.
  ```sh
  control-agent-server api GET /api/tools/ --expect 200 --check . len-ge 10 --check . contains terminal \
    --check . contains file_editor --check . contains task_tracker --check . contains browser_tool_set \
    --check . contains task_tool_set --check . contains planning_file_editor --check . contains glob \
    --check . contains ClassifyAndSwitchLLMTool --save F27.tools-list/tools
  ```
  The body is a plain array of names in registration order. `browser_tool_set`
  is listed whether or not Chromium is available, and some entries are class
  names (`ClassifyAndSwitchLLMTool`) that the catalog offers under their tool
  names.
- **Slash redirects (`F27.tools-redirect`).** Call both paths with the
  other trailing-slash form.
  ```sh
  NOSLASH=/api/tools
  control-agent-server api GET "$NOSLASH" --expect 307 --check-header location matches '/api/tools/$' --save F27.tools-redirect/no-slash
  control-agent-server api GET /api/tools/catalog/ --expect 307 --check-header location matches '/api/tools/catalog$'
  ```
  Both are `307` with a `Location` on this server. `api` reports the redirect
  instead of following it; HTTP clients that follow redirects (the TypeScript
  client calls `/api/tools/` directly) see the target.
- **Tool catalog (`F27.catalog`).** Read the catalog and compare it with the
  registry.
  ```sh
  control-agent-server api GET /api/tools/catalog --expect 200 --quiet --check tools len-ge 10 \
    --check tools matches '"name": "terminal", "user_selectable": true, "usable": true, "description": "[^"]+", "in_default_set": true' \
    --check tools matches '"name": "file_editor", "user_selectable": true, "usable": true, "description": "[^"]+", "in_default_set": true' \
    --check tools matches '"name": "task_tracker", "user_selectable": true, "usable": true, "description": "[^"]+", "in_default_set": true' \
    --check tools matches '"name": "grep", "user_selectable": true, "usable": true, "description": "[^"]+", "in_default_set": false' \
    --check tools matches '"name": "switch_llm", "user_selectable": true, "usable": true, "description": "[^"]+", "in_default_set": true' \
    --check tools matches '"name": "browser_tool_set", "user_selectable": true, "usable": (true|false), "description": "[^"]+", "in_default_set": true' \
    --check tools contains '{"name": "task", "user_selectable": false' \
    --check tools contains '{"name": "planning_file_editor", "user_selectable": false' \
    --check tools contains '{"name": "route_task_to_model", "user_selectable": false' \
    --check tools not-contains '"name": "ClassifyAndSwitchLLMTool"' \
    --check tools not-contains '"user_selectable": true, "usable": true, "description": ""' \
    --check tools not-contains '"user_selectable": true, "usable": false, "description": ""' --save F27.catalog/catalog
  ```
  `terminal`, `file_editor`, `task_tracker` and `switch_llm` (a built-in that
  is not in the registry) are selectable, usable and in the default set;
  `glob`/`grep` are selectable but not default; `task`,
  `planning_file_editor` and `route_task_to_model` (registered as
  `ClassifyAndSwitchLLMTool`) are internal. Every selectable entry has a
  description (internal entries have an empty one).
- **Project hooks (`F27.hooks-project`).** Load the fixture project's hooks.
  ```sh
  control-agent-server api POST /api/hooks --json '{"project_dir": "'"$P"'"}' --expect 200 \
    --check hook_config.pre_tool_use len-eq 1 --check hook_config.pre_tool_use.0.matcher eq '*' \
    --check hook_config.pre_tool_use.0.hooks.0.type eq command \
    --check hook_config.pre_tool_use.0.hooks.0.command eq 'echo QA_PROJECT_HOOK' \
    --check hook_config.pre_tool_use.0.hooks.0.timeout eq 60 --check hook_config.pre_tool_use.0.hooks.0.async eq false \
    --check hook_config.post_tool_use len-eq 0 --check hook_config.stop len-eq 0 --save F27.hooks-project/hooks
  jq -e '.hooks.PreToolUse[0].hooks[0].command == "echo QA_PROJECT_HOOK"' "$P/.openhands/hooks.json"
  ```
  The file on disk uses the legacy wrapper and `PreToolUse`; the answer uses
  `pre_tool_use`, the default timeout `60`, `async: false` and empty lists
  for the other five events.
- **Formats (`F27.hooks-formats`).** A snake_case file with a regex matcher
  and a PascalCase `Stop` next to it.
  ```sh
  control-agent-server api POST /api/hooks --json '{"project_dir": "'"$F/snake"'"}' --expect 200 \
    --check hook_config.pre_tool_use.0.matcher eq 'terminal|file_editor' \
    --check hook_config.pre_tool_use.0.hooks.0.type eq command \
    --check hook_config.pre_tool_use.0.hooks.0.command eq 'echo QA_SNAKE' \
    --check hook_config.pre_tool_use.0.hooks.0.timeout eq 7 --check hook_config.pre_tool_use.0.hooks.0.async eq true \
    --check hook_config.stop.0.matcher eq '*' --check hook_config.stop.0.hooks.0.type eq prompt \
    --check hook_config.stop.0.hooks.0.prompt eq 'QA done?' --check hook_config.stop.0.hooks.0.command eq '' \
    --save F27.hooks-formats/snake
  ```
  The matcher, timeout and `async` survive, the `type` defaults to `command`,
  `Stop` becomes `stop` with matcher `*`, and the prompt hook has an empty
  `command`.
- **No hooks (`F27.hooks-absent`).** Every way of having no project hooks.
  ```sh
  control-agent-server api POST /api/hooks --json '{}' --expect 200 --check hook_config missing --save F27.hooks-absent/no-project-dir
  control-agent-server api POST /api/hooks --json '{"project_dir": null}' --expect 200 --check hook_config missing
  control-agent-server api POST /api/hooks --json '{"projectDir": "'"$P"'"}' --expect 200 --check hook_config missing \
    --save F27.hooks-absent/misspelled-field
  control-agent-server api POST /api/hooks --json '{"project_dir": "'"$F/qa-missing-dir"'"}' --expect 200 --check hook_config missing
  control-agent-server api POST /api/hooks --json '{"project_dir": "'"$F/nohooks"'"}' --expect 200 --check hook_config missing
  control-agent-server api POST /api/hooks --json '{"project_dir": "'"$F/agentsdir"'"}' --expect 200 --check hook_config missing
  control-agent-server api POST /api/hooks --json '{"project_dir": "'"$F/empty"'"}' --expect 200 --check hook_config missing \
    --save F27.hooks-absent/empty-file
  test -s "$F/agentsdir/.agents/hooks.json"
  ```
  All seven answer `200 {"hook_config": null}`; a valid file under
  `.agents/hooks.json` is not read. `projectDir` names the same project that
  `F27.hooks-project` loads, but unknown fields are ignored, so the request
  reads as "no project".
- **Broken hooks.json (`F27.hooks-invalid`).** Errors are swallowed into
  `null`; only the log tells them apart from "no hooks".
  ```sh
  H0=$(control-agent-server logs --grep 'Failed to load hooks' | jq -r .total_matching)
  for d in badjson unknown snakeunknown both nocmd; do
    control-agent-server api POST /api/hooks --json '{"project_dir": "'"$F/$d"'"}' --expect 200 --check hook_config missing \
      --save "F27.hooks-invalid/$d"
  done
  control-agent-server logs --grep 'Failed to load hooks' --expect-min "$((H0 + 5))"
  ```
  Each answer is `200 {"hook_config": null}` and these five requests add a
  `Failed to load hooks from ...` warning each to the server log. The log is
  appended across restarts and replays, hence the count from `H0`.
- **Sub-agent built-ins (`F27.subagents-builtin`).** List with only the
  built-ins, then with none.
  ```sh
  control-agent-server api POST /api/sub-agents --json '{"load_user": false, "load_project": false}' --expect 200 \
    --check agents len-eq 4 --check agents.0.name eq bash-runner --check agents.1.name eq code-explorer \
    --check agents.2.name eq general-purpose --check agents.3.name eq web-researcher \
    --check agents.0.level eq builtin --check agents.0.is_builtin eq true --check agents.3.is_builtin eq true \
    --check agents.2.tools contains file_editor --check agents.3.tools.0 eq browser_tool_set \
    --check agents.3.mcp_config.fetch.command eq uvx --check agents.3.mcp_servers.fetch.command eq uvx \
    --check agents.0.source matches 'preset/subagents/bash_runner\.md$' --save F27.subagents-builtin/builtins
  control-agent-server api POST /api/sub-agents --json '{"load_user": false, "project_dir": "'"$F/qa-missing-dir"'"}' \
    --expect 200 --check agents len-eq 4 --check agents.0.name eq bash-runner
  control-agent-server api POST /api/sub-agents --json '{"load_user": false, "load_project": false, "load_builtin": false}' \
    --expect 200 --check agents len-eq 0
  ```
  Four built-ins in file order, all `level: builtin` with their bundled
  source file; `web-researcher` needs `browser_tool_set` and an MCP fetch
  server. A `project_dir` that does not exist is not an error and adds
  nothing. Without built-ins the list is empty.
- **Project sub-agents (`F27.subagents-project`).** List the project's agents
  without user agents.
  ```sh
  control-agent-server api POST /api/sub-agents --json '{"load_user": false, "project_dir": "'"$P"'"}' --expect 200 \
    --check agents len-eq 7 --check agents.1.name eq qa-helper --check agents.1.level eq project \
    --check agents.1.is_builtin eq false --check agents.1.system_prompt contains QA_SUBAGENT \
    --check agents.2.name eq qa-reviewer --check agents.2.source eq "$P/.agents/agents/qa-reviewer.md" \
    --check agents.2.color eq purple --check agents.2.tools len-eq 2 --check agents.2.skills.0 eq qa-f27-proj-skill \
    --check agents.2.permission_mode eq never_confirm --check agents.2.max_iteration_per_run eq 5 \
    --check agents.2.when_to_use_examples.0 eq 'review the qa fixture' --check agents.2.metadata.qa_extra eq QA_META \
    --check agents.2.model eq inherit --check agents.2.system_prompt eq 'You are the QA reviewer. Say QA_REVIEWER_MARKER.' \
    --check agents.3.name eq qa-legacy-agent --check agents.3.source eq "$P/.openhands/agents/qa-legacy-agent.md" \
    --check agents.4.name eq bash-runner --save F27.subagents-project/project
  ```
  Project agents come first in file order (`.agents/agents`, then
  `.openhands/agents`), then the built-ins. `qa-legacy-agent` has no `name`
  in its frontmatter and is named after its file; unknown frontmatter keys
  land in `metadata`.
- **User sub-agents (`F27.subagents-user`).** User agents alone, then
  switched off.
  ```sh
  control-agent-server api POST /api/sub-agents --json '{"load_project": false, "load_builtin": false, "project_dir": "'"$P"'"}' \
    --expect 200 --check agents len-eq 3 --check agents.0.name eq qa-helper --check agents.0.level eq user \
    --check agents.0.system_prompt eq QA_USER_COPY --check agents.1.name eq qa-user-agent \
    --check agents.2.name eq qa-persist-agent --check agents.2.source eq "$H/.openhands/agents/qa-persist-agent.md" \
    --save F27.subagents-user/user-only
  control-agent-server api POST /api/sub-agents --json '{"load_user": false, "load_builtin": false, "project_dir": "'"$P"'"}' \
    --expect 200 --check agents not-contains qa-user-agent --check agents not-contains qa-persist-agent \
    --check agents not-contains QA_USER_COPY
  ```
  `~/.agents/agents` comes before `$OH_PERSISTENCE_DIR/agents` (both under
  the run's `HOME` here), every entry is `level: user`, and the project's
  agents stay out with `load_project: false`. With `load_user: false` no user
  agent is listed.
- **Precedence (`F27.subagents-precedence`).** Everything on: project, user
  and built-in names collide.
  ```sh
  control-agent-server api POST /api/sub-agents --json '{"project_dir": "'"$P"'"}' --expect 200 --check agents len-eq 9 \
    --check agents.0.name eq general-purpose --check agents.0.level eq project \
    --check agents.0.description eq 'QA override of a built-in' --check agents.0.is_builtin eq false \
    --check agents.1.name eq qa-helper --check agents.1.level eq project --check agents.1.system_prompt contains QA_SUBAGENT \
    --check agents.2.name eq qa-reviewer --check agents.2.system_prompt contains QA_REVIEWER_MARKER \
    --check agents not-contains QA_SHADOWED --check agents not-contains QA_USER_COPY \
    --check agents.4.name eq qa-user-agent --check agents.5.name eq qa-persist-agent \
    --check agents.6.name eq bash-runner --check agents.8.name eq web-researcher --save F27.subagents-precedence/all
  ```
  Nine unique names: the project's `general-purpose` replaces the built-in,
  the project's `qa-helper` hides the user copy, and the `.openhands/agents`
  copy of `qa-reviewer` is hidden by the `.agents/agents` one.
- **Invalid definitions (`F27.subagents-invalid`).** The project holds two
  invalid agents, a README, a nested directory and a text file.
  ```sh
  W0=$(control-agent-server logs --grep 'Failed to load agent definition' | jq -r .total_matching)
  control-agent-server api POST /api/sub-agents --json '{"load_user": false, "load_builtin": false, "project_dir": "'"$P"'"}' \
    --expect 200 --check agents len-eq 4 --check agents not-contains qa-bad-mode --check agents not-contains qa-bad-iter \
    --check agents not-contains qa-readme --check agents not-contains qa-nested --check agents not-contains qa-notes \
    --save F27.subagents-invalid/project-only
  test -f "$P/.agents/agents/qa-bad-mode.md"
  control-agent-server logs --grep 'Failed to load agent definition' --expect-min "$((W0 + 2))"
  ```
  Only the four valid project agents are listed; this request adds two
  `Failed to load agent definition from ...` warnings to the log, one per
  invalid file (the file name wraps onto the next log line). Every earlier
  request that read the project logged them too, hence the count from `W0`.
- **Agent with a missing skill (`F27.subagents-bad-skill`), known bug.** The
  catalog accepts the agent; a conversation in that workspace cannot take a
  message.
  ```sh
  control-agent-server api POST /api/sub-agents --json '{"load_user": false, "load_builtin": false, "project_dir": "'"$F/badskill"'"}' \
    --expect 200 --check agents len-eq 1 --check agents.0.name eq qa-bad-skill --check agents.0.skills.0 eq qa-f27-missing-skill
  GCID=$(control-agent-server conversation start --workspace "$F/nohooks" --no-run --tools terminal --no-autotitle --print-id)
  control-agent-server api POST "/api/conversations/$GCID/events" \
    --json '{"role": "user", "content": [{"type": "text", "text": "hi"}], "run": false}' --expect 2xx \
    --save F27.subagents-bad-skill/send-control
  BCID=$(control-agent-server conversation start --workspace "$F/badskill" --no-run --tools terminal --no-autotitle --print-id)
  control-agent-server api POST "/api/conversations/$BCID/events" --json '{"role": "user", "content": [{"type": "text", "text": "hi"}], "run": false}' --expect 2xx,4xx --save F27.subagents-bad-skill/send  # bug
  ```
  The same message to a conversation in a workspace without project agents
  is accepted (the control). A broken sub-agent definition should cost only
  that sub-agent (skipped with a warning, as invalid frontmatter is) or be a
  client error naming it.
  Today the message `POST` is `500` with `Skill 'qa-f27-missing-skill' not
  found but was given to agent 'qa-bad-skill'.`: the conversation registers
  every project and user agent file on its first message and
  `agent_definition_to_factory` raises for a skill it cannot resolve. With an
  `initial_message` in the create request, `POST /api/conversations` itself
  is the 500, and the conversation is stored anyway (it is listed, `idle`).
  User agents (`~/.agents/agents`) are registered for every workspace: the
  same file there made the first message of a conversation in an unrelated
  workspace a 500 when this bullet was reviewed (not driven here, because a
  failing bullet would leave the file behind for the later conversations).
- **Hooks in a conversation (`F27.hooks-conversation`).** Load the project's
  hooks, start a DeepSeek conversation with them, and look for the hook.
  ```sh
  HC=$(control-agent-server api POST /api/hooks --json '{"project_dir": "'"$P"'"}' --field hook_config)
  HCID=$(control-agent-server conversation start --workspace "$P" --tools terminal --no-autotitle \
    --body-json '{"hook_config": '"$HC"'}' --prompt 'Run this exact shell command with the terminal tool: ls' \
    --wait --timeout 300 --print-id)
  control-agent-server conversation events "$HCID" --kinds ObservationEvent --contains AGENTS.md --expect-kind ObservationEvent
  control-agent-server conversation events "$HCID" --kinds HookExecutionEvent --contains '"hook_event_type": "PreToolUse"' \
    --expect-kind HookExecutionEvent
  control-agent-server conversation events "$HCID" --kinds HookExecutionEvent --contains '"stdout": "QA_PROJECT_HOOK\n"' \
    --expect-kind HookExecutionEvent --full --save F27.hooks-conversation/hook-events
  control-agent-server ws listen "/sockets/events/$HCID" --query resend_mode=all --until-kind HookExecutionEvent \
    --expect-kind HookExecutionEvent --duration 20 --save F27.hooks-conversation/ws-replay
  control-agent-server restart
  control-agent-server api GET "/api/conversations/$HCID" --expect 200 --check hook_config.pre_tool_use.0.matcher eq '*' \
    --check hook_config.pre_tool_use.0.hooks.0.command eq 'echo QA_PROJECT_HOOK' --quiet --save F27.hooks-conversation/after-restart
  ```
  The agent's `ls` observation lists `AGENTS.md`, and a `HookExecutionEvent`
  with `hook_event_type: PreToolUse`, `tool_name: terminal`, `hook_command:
  echo QA_PROJECT_HOOK`, the command's output in `stdout` and `exit_code: 0`
  is in the event log and in the WebSocket replay. The conversation still
  reports the same `hook_config` after the restart.
- **Hooks are not loaded implicitly (`F27.hooks-not-automatic`).** The same
  project and prompt, without `hook_config`.
  ```sh
  NCID=$(control-agent-server conversation start --workspace "$P" --tools terminal --no-autotitle \
    --prompt 'Run this exact shell command with the terminal tool: ls' --wait --timeout 300 --print-id)
  control-agent-server conversation events "$NCID" --kinds ObservationEvent --contains AGENTS.md --expect-kind ObservationEvent
  control-agent-server conversation events "$NCID" --kinds HookExecutionEvent --expect-count 0 --save F27.hooks-not-automatic/no-hooks
  control-agent-server api GET "/api/conversations/$NCID" --expect 200 --check hook_config missing --quiet
  ```
  The terminal ran (`AGENTS.md` observed) but no `HookExecutionEvent` exists
  and `hook_config` is `null`: a client that wants project hooks must call
  `POST /api/hooks` and pass the result, as `RemoteConversation.loadHooks`
  callers do.
- **Blocking hooks (`F27.hooks-block`).** A `PreToolUse` hook on `terminal`
  that exits 2, then a `UserPromptSubmit` hook that exits 2 on a queued
  message.
  ```sh
  mkdir -p "$F/blockws"
  BLK=$(control-agent-server conversation start --workspace "$F/blockws" --tools terminal --no-autotitle --max-iterations 6 \
    --body-json '{"hook_config": {"pre_tool_use": [{"matcher": "terminal", "hooks": [{"command": "echo QA_F27_DENIED >&2; exit 2"}]}]}}' \
    --prompt 'If the terminal call is blocked, do not retry and finish. Run this exact shell command with the terminal tool: touch qa_f27_blocked.txt' \
    --wait --timeout 300 --print-id)
  control-agent-server api GET "/api/conversations/$BLK" --expect 200 --check execution_status eq finished --check blocked_actions len-eq 0 --quiet
  control-agent-server conversation events "$BLK" --kinds ActionEvent --contains 'touch qa_f27_blocked.txt' --expect-kind ActionEvent
  control-agent-server conversation events "$BLK" --kinds HookExecutionEvent --contains '"blocked": true' --expect-min 1 \
    --full --save F27.hooks-block/pre-tool-use
  control-agent-server conversation events "$BLK" --kinds HookExecutionEvent --contains '"exit_code": 2' --expect-min 1
  control-agent-server conversation events "$BLK" --kinds UserRejectObservation --contains '"rejection_source": "hook"' --expect-min 1
  control-agent-server conversation events "$BLK" --kinds UserRejectObservation --contains '"rejection_reason": "QA_F27_DENIED"' --expect-min 1
  control-agent-server conversation events "$BLK" --key blocked_actions --contains QA_F27_DENIED --expect-min 1
  control-agent-server conversation events "$BLK" --kinds ObservationEvent --contains '"tool_name": "terminal"' --expect-count 0
  test ! -e "$F/blockws/qa_f27_blocked.txt"
  UPS=$(control-agent-server conversation start --workspace "$F/blockws" --tools terminal --no-autotitle --no-run \
    --body-json '{"hook_config": {"user_prompt_submit": [{"hooks": [{"command": "echo QA_F27_PROMPT_DENIED >&2; exit 2"}]}]}}' \
    --prompt 'Run this exact shell command with the terminal tool: touch qa_f27_prompt.txt' --print-id)
  control-agent-server api GET "/api/conversations/$UPS" --expect 200 --check execution_status eq idle \
    --check blocked_messages matches QA_F27_PROMPT_DENIED --quiet --save F27.hooks-block/prompt-blocked
  control-agent-server conversation events "$UPS" --kinds HookExecutionEvent --contains '"hook_event_type": "UserPromptSubmit"' --expect-count 1
  control-agent-server conversation events "$UPS" --kinds HookExecutionEvent --contains '"blocked": true' --expect-count 1
  control-agent-server api POST "/api/conversations/$UPS/run" --expect 200
  control-agent-server conversation wait "$UPS" --until finished,error,stuck --timeout 60
  control-agent-server api GET "/api/conversations/$UPS" --expect 200 --check execution_status eq finished --check blocked_messages len-eq 0 --quiet
  control-agent-server conversation events "$UPS" --kinds MessageEvent --contains '"source": "user"' --expect-count 1
  control-agent-server conversation events "$UPS" --kinds ActionEvent,MessageEvent --contains '"source": "agent"' --expect-count 0 \
    --save F27.hooks-block/prompt-no-agent-turn
  test ! -e "$F/blockws/qa_f27_prompt.txt"
  ```
  The agent asked for the terminal (its `ActionEvent` is there), the hook
  ran first with `blocked: true` and `exit_code: 2`, and the agent got a
  `UserRejectObservation` with `rejection_source: hook` and the hook's stderr
  as `rejection_reason`; no terminal observation exists and the file was not
  created. `blocked_actions` held the action id while the step ran (a
  `ConversationStateUpdateEvent`) and is empty once the agent consumed it, so
  a client reads it from the events, not from a later GET. The blocked
  prompt is held in `blocked_messages` (keyed by the message id) while the
  conversation is idle; running it consumes the entry and finishes with no
  agent event and no model call. The positive control is
  `F27.hooks-conversation`: the same terminal call with a hook that exits 0
  runs.
- **Client tools stay out of the catalog (`F27.catalog-sealed`).** Register a
  client tool through a conversation (no model needed).
  ```sh
  CCID=$(control-agent-server conversation start --no-run --no-autotitle --tools terminal \
    --body-json '{"client_tools": [{"name": "qa_f27_client_tool", "description": "QA client-side tool"}]}' --print-id)
  control-agent-server api GET "/api/conversations/$CCID" --expect 200 --check agent.tools contains qa_f27_client_tool --quiet
  control-agent-server api GET /api/tools/catalog --expect 200 --quiet --check tools not-contains qa_f27_client_tool \
    --check tools contains '"name": "terminal"' --save F27.catalog-sealed/catalog
  ```
  The conversation's agent has the client tool (with its spec in `params`);
  the catalog, sealed when the server started, does not.
- **Client tool call (`F27.client-tool-call`).** A client registers
  `qa_f27_call_tool` (one required `marker` argument), listens on the events
  socket and runs the conversation; then the registration errors; then a
  restart and a second call. The conversation has no server tools
  (`--tools none`), so the client tool is the only one it can call.
  ```sh
  CTW="$F/clienttool"
  mkdir -p "$CTW"
  CTSPEC='{"name": "qa_f27_call_tool", "description": "Record a QA marker in the client. Call it exactly once when asked.", "parameters": {"type": "object", "properties": {"marker": {"type": "string", "description": "The marker text."}}, "required": ["marker"]}}'
  CT=$(control-agent-server conversation start --workspace "$CTW" --tools none --no-autotitle --no-run \
    --body-json '{"client_tools": ['"$CTSPEC"']}' \
    --prompt 'Call the qa_f27_call_tool tool exactly once with marker set to QA_F27_CALL, then finish.' --print-id)
  control-agent-server ws start "/sockets/events/$CT" --name qa-f27-client --duration 300
  control-agent-server api POST "/api/conversations/$CT/run" --expect 200
  control-agent-server conversation wait "$CT" --until finished,error,stuck --timeout 300
  control-agent-server ws stop qa-f27-client --kinds ActionEvent --contains ClientAction_qa_f27_call_tool --expect-min 1 --wait 15 \
    --save F27.client-tool-call/ws
  control-agent-server api GET "/api/conversations/$CT" --expect 200 --check execution_status eq finished \
    --check client_tools.0.name eq qa_f27_call_tool --check agent.tools contains qa_f27_call_tool --quiet
  control-agent-server conversation events "$CT" --kinds ActionEvent --contains '"marker": "QA_F27_CALL"' --expect-kind ActionEvent
  control-agent-server conversation events "$CT" --kinds ObservationEvent --contains 'Tool call dispatched to client.' \
    --expect-kind ObservationEvent --full --save F27.client-tool-call/ack
  OFF='{"llm": {"model": "openai/qa-f27-offline", "api_key": "qa-f27-offline-key", "base_url": "http://127.0.0.1:9/v1", "num_retries": 0}, "tools": []}'
  N1=$(control-agent-server api GET /api/conversations/count --field .)
  control-agent-server api POST /api/conversations --json "{\"workspace\": {\"working_dir\": \"$CTW\"}, \"agent\": $OFF, \"client_tools\": [$CTSPEC]}" \
    --expect 201 --check client_tools.0.name eq qa_f27_call_tool --quiet
  control-agent-server api POST /api/conversations \
    --json "{\"workspace\": {\"working_dir\": \"$CTW\"}, \"agent\": $OFF, \"client_tools\": [{\"name\": \"qa_f27_dup\", \"description\": \"a\"}, {\"name\": \"qa_f27_dup\", \"description\": \"b\"}]}" \
    --expect 422 --check detail contains 'Duplicate client tool name' --save F27.client-tool-call/duplicate
  control-agent-server api POST /api/conversations \
    --json "{\"workspace\": {\"working_dir\": \"$CTW\"}, \"agent\": $OFF, \"client_tools\": [{\"name\": \"terminal\", \"description\": \"QA shadow\"}]}" \
    --expect 422 --check detail contains 'collides with an existing non-client tool' --save F27.client-tool-call/server-name
  control-agent-server api POST /api/conversations \
    --json "{\"workspace\": {\"working_dir\": \"$CTW\"}, \"agent\": $OFF, \"client_tools\": [{\"name\": \"qa_f27_call_tool\", \"description\": \"QA\", \"parameters\": {\"type\": \"object\", \"properties\": {\"other\": {\"type\": \"integer\"}}}}]}" \
    --expect 422 --check detail contains 'already registered with a different parameters schema' --save F27.client-tool-call/schema-conflict
  control-agent-server api GET /api/conversations/count --expect 200 --check . eq "$((N1 + 1))"
  control-agent-server api GET /api/tools/ --expect 200 --check . contains qa_f27_call_tool --check . not-contains qa_f27_dup --quiet
  control-agent-server restart
  control-agent-server api GET /api/tools/ --expect 200 --check . not-contains qa_f27_call_tool --quiet
  control-agent-server conversation send "$CT" --text 'Call the qa_f27_call_tool tool exactly once more with marker set to QA_F27_AGAIN, then finish.' \
    --wait --timeout 300
  control-agent-server conversation events "$CT" --kinds ActionEvent --contains '"marker": "QA_F27_AGAIN"' --expect-kind ActionEvent \
    --save F27.client-tool-call/after-restart
  control-agent-server api GET /api/tools/ --expect 200 --check . contains qa_f27_call_tool --quiet
  ```
  A client sees its tool call on the socket as an `ActionEvent` with
  `tool_name: qa_f27_call_tool`, an action of kind
  `ClientAction_qa_f27_call_tool` and the model's `marker`; the server
  answers it at once with a `ClientToolObservation` (`Tool call dispatched to
  client.`) and the agent finishes, so the client's own result never
  reaches the model through this path. The second create shows that the same
  name with the same schema is accepted; the duplicate, the server-tool name
  and the changed schema are each `422` with the reason, and the count grows
  by that one create only. After a restart nothing registers the tool until
  its conversation is loaded; the next message re-registers it from the
  stored `client_tools` and the agent calls it again. Forks keep
  `client_tools` too (fork family).
- **Forwarded sub-agent definitions (`F27.agent-definitions`).** A client
  forwards `qa-f27-delegate` with the create request and asks the agent to
  delegate a command to it through the task tool.
  ```sh
  DW="$F/delegate"
  mkdir -p "$DW"
  DCID=$(control-agent-server conversation start --workspace "$DW" --tools task_tool_set --no-autotitle --max-iterations 10 \
    --body-json '{"agent_definitions": [{"name": "qa-f27-delegate", "description": "QA delegate that creates files on request.", "tools": ["terminal"], "system_prompt": "You are the QA delegate. Do exactly what the task asks with the terminal tool, then finish."}]}' \
    --prompt 'Use the task tool with subagent_type qa-f27-delegate and this prompt: "Run this exact shell command with the terminal tool: echo QA_F27_DELEGATED > qa_f27_delegated.txt". Do not run anything yourself. Then finish.' \
    --wait --timeout 400 --print-id)
  control-agent-server api GET "/api/conversations/$DCID" --expect 200 --check execution_status eq finished --quiet
  control-agent-server conversation events "$DCID" --kinds SystemPromptEvent --contains 'QA delegate that creates files on request.' --expect-count 1
  control-agent-server conversation events "$DCID" --kinds ActionEvent --contains '"subagent_type": "qa-f27-delegate"' --expect-kind ActionEvent
  control-agent-server conversation events "$DCID" --kinds ObservationEvent --contains '"subagent": "qa-f27-delegate"' --expect-kind ObservationEvent \
    --full --save F27.agent-definitions/task
  grep -qx QA_F27_DELEGATED "$DW/qa_f27_delegated.txt"
  control-agent-server state grep qa-f27-delegate --glob "server/workspace/conversations/${DCID//-/}/meta.json"
  control-agent-server api POST /api/sub-agents --json '{"project_dir": "'"$DW"'"}' --expect 200 --check agents contains bash-runner \
    --check agents not-contains qa-f27-delegate --save F27.agent-definitions/catalog
  ```
  The task tool's description in the conversation's system prompt lists
  `qa-f27-delegate` with the forwarded description, the agent's `task`
  action names it, and the `TaskObservation` reports `subagent:
  qa-f27-delegate`; the delegate ran the command in the same workspace, so
  the file holds `QA_F27_DELEGATED`. The definition is stored in the
  conversation's `meta.json` for resume, and the sub-agents catalog, which
  reads only files and built-ins, does not list it.
- **Custom tool module (`F27.custom-tool-modules`).** The documented
  custom-tool deployment: `OH_EXTRA_PYTHON_PATH` names the module directory
  and the client sends `tool_module_qualnames`.
  ```sh
  MW="$F/modulews"
  mkdir -p "$MW"
  control-agent-server api GET /api/tools/ --expect 200 --check . contains terminal --check . not-contains qa_f27_echo --quiet
  X0=$(control-agent-server logs --grep 'Extended sys.path with 1 directory' | jq -r .total_matching)
  control-agent-server restart --env OH_EXTRA_PYTHON_PATH="$F/pytools"
  control-agent-server logs --grep 'Extended sys.path with 1 directory' --expect-min "$((X0 + 1))"
  control-agent-server api GET /api/tools/ --expect 200 --check . not-contains qa_f27_echo --quiet
  MCID=$(control-agent-server conversation start --workspace "$MW" --tools qa_f27_echo --no-autotitle \
    --body-json '{"tool_module_qualnames": {"qa_f27_echo": "qa_f27_tools"}}' \
    --prompt 'Call the qa_f27_echo tool exactly once with marker QA_F27_MODULE, then finish.' --wait --timeout 300 --print-id)
  control-agent-server api GET "/api/conversations/$MCID" --expect 200 --check execution_status eq finished \
    --check tool_module_qualnames.qa_f27_echo eq qa_f27_tools --check agent.tools contains qa_f27_echo --quiet
  control-agent-server conversation events "$MCID" --kinds ObservationEvent --contains '"kind": "QaF27EchoObservation"' --expect-count 1
  control-agent-server conversation events "$MCID" --kinds ObservationEvent --contains 'QA_F27_ECHO_RAN QA_F27_MODULE' --expect-min 1 \
    --save F27.custom-tool-modules/observation
  control-agent-server api GET /api/tools/ --expect 200 --check . contains qa_f27_echo --quiet --save F27.custom-tool-modules/tools
  control-agent-server api GET /api/tools/catalog --expect 200 --check tools not-contains qa_f27_echo --check tools contains '"name": "terminal"' --quiet
  M0=$(control-agent-server logs --grep 'qa_f27_missing_module' | jq -r .total_matching)
  OFF='{"llm": {"model": "openai/qa-f27-offline", "api_key": "qa-f27-offline-key", "base_url": "http://127.0.0.1:9/v1", "num_retries": 0}, "tools": []}'
  control-agent-server api POST /api/conversations \
    --json "{\"workspace\": {\"working_dir\": \"$MW\"}, \"agent\": $OFF, \"tool_module_qualnames\": {\"qa_f27_missing\": \"qa_f27_missing_module\"}}" \
    --expect 201 --check tool_module_qualnames.qa_f27_missing eq qa_f27_missing_module --quiet --save F27.custom-tool-modules/missing-module
  control-agent-server logs --grep 'qa_f27_missing_module' --expect-min "$((M0 + 1))"
  control-agent-server restart
  control-agent-server api GET /api/tools/ --expect 200 --check . not-contains qa_f27_echo --quiet
  control-agent-server conversation send "$MCID" --text 'Call the qa_f27_echo tool exactly once more with marker QA_F27_RESUMED, then finish.' \
    --wait --timeout 300
  control-agent-server conversation events "$MCID" --kinds ObservationEvent --contains 'QA_F27_ECHO_RAN QA_F27_RESUMED' --expect-min 1 \
    --save F27.custom-tool-modules/after-restart
  control-agent-server api GET /api/tools/ --expect 200 --check . contains qa_f27_echo --quiet
  ```
  `OH_EXTRA_PYTHON_PATH` only extends `sys.path` (logged at startup); the
  create imports `qa_f27_tools`, whose import registers `qa_f27_echo`, and
  the agent's call returns the module's `QaF27EchoObservation`. The
  conversation reports the mapping, and the registry lists the tool from
  then on for every caller, while the catalog, sealed at startup, does not
  offer it. An unimportable module is only a warning: the create is `201`
  and keeps the mapping (the agent would fail later only if it names that
  tool). After a restart the registry no longer has the tool; loading the
  conversation for the next message imports the module again and the tool
  runs.
- **Startup imports (`F27.import-modules`).** `--import-modules` is a
  server argument that `launch` cannot pass (a harness gap), so the bullet
  starts this checkout's server in plain shell with a private `HOME`, state
  directory and key, attaches to it, and stops it before the bullet ends; a
  second start with a missing module must fail.
  ```sh
  QA_PY="$(jq -r .checkout "$AGENT_SERVER_VERIFY_RUN/run.json")/.venv/bin/python"
  IMP="$F/import-server"
  mkdir -p "$IMP/home" "$F/import-missing/home"
  IPORT=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
  (cd "$IMP" && exec env -i PATH="$PATH" HOME="$IMP/home" OH_PERSISTENCE_DIR="$IMP/persist" TMUX_TMPDIR="$IMP/tmux" \
    OH_SESSION_API_KEYS_0=qa-f27-import-key OH_EXTRA_PYTHON_PATH="$F/pytools" NO_PROXY=127.0.0.1,localhost COLUMNS=400 \
    timeout 300 "$QA_PY" -m openhands.agent_server --host 127.0.0.1 --port "$IPORT" --import-modules qa_f27_tools) > "$IMP/server.log" 2>&1 &
  IPID=$!
  IRUN=""
  trap 'if [ -n "$IRUN" ]; then control-agent-server stop --run "$IRUN" > /dev/null 2>&1; fi; kill -TERM "$IPID" 2>/dev/null || true' EXIT
  export QA_F27_IMPORT_KEY=qa-f27-import-key
  IRUN=$(control-agent-server attach --url "http://127.0.0.1:$IPORT" --key-env QA_F27_IMPORT_KEY --name f27-import --print-run)
  curl -s --noproxy '*' --retry 60 --retry-connrefused --retry-delay 1 --retry-max-time 120 -o /dev/null "http://127.0.0.1:$IPORT/alive"
  control-agent-server api GET /api/tools/ --run "$IRUN" --expect 200 --check . contains qa_f27_echo --check . contains terminal \
    --quiet --save F27.import-modules/tools
  control-agent-server api GET /api/tools/catalog --run "$IRUN" --expect 200 --quiet \
    --check tools contains '{"name": "qa_f27_echo", "user_selectable": true, "usable": true' --save F27.import-modules/catalog
  control-agent-server api GET /api/conversations/count --run "$IRUN" --expect 200 --check . eq 0
  control-agent-server api GET /api/tools/ --run "$IRUN" --auth none --expect 401
  grep -q 'Imported module: qa_f27_tools' "$IMP/server.log"
  control-agent-server stop --run "$IRUN"
  kill -TERM "$IPID"
  wait "$IPID" || true
  MPORT=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
  if (cd "$F/import-missing" && exec env -i PATH="$PATH" HOME="$F/import-missing/home" OH_PERSISTENCE_DIR="$F/import-missing/persist" \
    OH_SESSION_API_KEYS_0=qa-f27-import-key OH_EXTRA_PYTHON_PATH="$F/pytools" NO_PROXY=127.0.0.1,localhost COLUMNS=400 \
    timeout 120 "$QA_PY" -m openhands.agent_server --host 127.0.0.1 --port "$MPORT" --import-modules qa_f27_missing_module) > "$F/import-missing/server.log" 2>&1; then false; fi
  grep -q "ModuleNotFoundError: No module named 'qa_f27_missing_module'" "$F/import-missing/server.log"
  if grep -q 'Uvicorn running' "$F/import-missing/server.log"; then false; fi
  test ! -e "$F/import-missing/persist"
  ```
  With `--import-modules qa_f27_tools` the tool is registered before the
  app starts: `GET /api/tools/` lists `qa_f27_echo` with no conversation on
  the server, and the catalog, sealed after startup imports, offers it as a
  selectable, usable tool (with an empty `description`: the tool sets no
  `catalog_description`). A missing module is fatal: the process exits
  non-zero with the `ModuleNotFoundError` before uvicorn starts, so nothing
  is served and no state is written. `api --until-ok` gives up on a refused connection,
  hence the `curl --retry-connrefused` wait.
- **Tool module gone on resume (`F27.custom-tool-missing-on-resume`), known bug.**
  Restart the run without `OH_EXTRA_PYTHON_PATH` and use the conversation of
  `F27.custom-tool-modules`, whose events include `qa_f27_echo` calls.
  ```sh
  W0=$(control-agent-server logs --grep 'Failed to import module' | jq -r .total_matching)
  control-agent-server restart --reset-config
  control-agent-server api GET "/api/conversations/$MCID" --expect 200 --check tool_module_qualnames.qa_f27_echo eq qa_f27_tools --quiet
  control-agent-server api POST "/api/conversations/$MCID/events" --json '{"role": "user", "content": [{"type": "text", "text": "Reply with the single word ok."}], "run": false}' --expect 2xx,4xx --save F27.custom-tool-missing-on-resume/send  # bug
  control-agent-server logs --grep 'Failed to import module' --expect-min "$((W0 + 1))"
  control-agent-server api GET "/api/conversations/$MCID/events/search" --expect 200 --check items len-ge 1 --quiet  # bug
  ```
  `_prepare_persisted_runtime` logs `Failed to import module 'qa_f27_tools'
  ... Tool will not be available.` and promises to go on without the tool.
  Today the conversation cannot be loaded at all: its stored events hold a
  `QaF27EchoObservation`, whose class only that module defines, so the
  message `POST` and `GET .../events/search` are `500` with `Unknown kind
  'QaF27EchoObservation'`. Only `GET /api/conversations/{id}`, which reads
  the metadata without loading, still answers. A deployment that drops or
  renames a custom tool module loses every conversation that used it.
- **Playwright browsers directory (`F27.browser-playwright-path`), known bug.**
  Point the server at the run-owned Playwright directory through
  `PLAYWRIGHT_BROWSERS_PATH` only.
  ```sh
  control-agent-server api GET /api/tools/catalog --expect 200 --quiet \
    --check tools contains '{"name": "browser_tool_set", "user_selectable": true, "usable": false'
  control-agent-server restart --env PLAYWRIGHT_BROWSERS_PATH="$PW"
  control-agent-server api GET /api/tools/catalog --expect 200 --quiet --check tools contains '{"name": "terminal", "user_selectable": true, "usable": true'
  control-agent-server api GET /api/tools/catalog --expect 200 --quiet --check tools contains '{"name": "browser_tool_set", "user_selectable": true, "usable": true' --save F27.browser-playwright-path/catalog  # bug
  ```
  The first check shows this server finds no Chromium on its own (no
  standard path, nothing in its private `~/.cache/ms-playwright`). Playwright
  installs browsers into `$PLAYWRIGHT_BROWSERS_PATH` when it is set, so the
  probe should look there. Today `check_chromium_available()` ignores the
  variable and the catalog keeps `usable: false`, and the browser tool cannot
  start on such a machine even though Playwright could launch the binary.
- **Browser probe (`F27.browser-probe`).** Restart without the earlier
  overrides and see the browser unusable, then put the same Chromium where
  the probe looks (the server's `HOME`) and restart again.
  ```sh
  control-agent-server restart --reset-config
  control-agent-server api GET /api/tools/catalog --expect 200 --quiet \
    --check tools contains '{"name": "browser_tool_set", "user_selectable": true, "usable": false' \
    --save F27.browser-probe/catalog-before
  control-agent-server api GET /server_info --auth none --expect 200 --check usable_tools contains terminal \
    --check usable_tools not-contains browser_tool_set
  mkdir -p "$H/.cache"
  ln -sfn "$PW" "$H/.cache/ms-playwright"
  control-agent-server restart
  control-agent-server api GET /api/tools/catalog --expect 200 --quiet \
    --check tools contains '{"name": "browser_tool_set", "user_selectable": true, "usable": true' \
    --save F27.browser-probe/catalog
  control-agent-server api GET /server_info --auth none --expect 200 --check usable_tools contains browser_tool_set \
    --save F27.browser-probe/server-info
  ```
  Before the link both views leave the browser out; after it the catalog
  reports the browser usable and `usable_tools` lists it. The probe result is
  cached per process, so a Chromium installed while the server runs shows up
  only after a restart.
- **Default tools follow the browser setting (`F27.browser-disabled-launch`).**
  Start conversations from the saved settings (no `--tools`, so the server
  picks the default tool set) on the server of the previous bullet, which
  finds Chromium, and again after a restart with `enable_browser: false`.
  ```sh
  BON=$(control-agent-server conversation start --no-run --no-autotitle --print-id)
  control-agent-server api GET "/api/conversations/$BON" --expect 200 --check agent.tools contains browser_tool_set \
    --check agent.tools contains terminal --quiet --save F27.browser-disabled-launch/enabled
  control-agent-server restart --config-json '{"enable_browser": false}'
  BOFF=$(control-agent-server conversation start --no-run --no-autotitle --print-id)
  control-agent-server api GET "/api/conversations/$BOFF" --expect 200 --check agent.tools not-contains browser_tool_set \
    --check agent.tools contains terminal --check agent.tools contains file_editor --quiet --save F27.browser-disabled-launch/disabled
  control-agent-server api GET "/api/conversations/$BON" --expect 200 --check agent.tools contains browser_tool_set --quiet
  test -L "$H/.cache/ms-playwright"
  ```
  With Chromium found, the default set is `terminal`, `file_editor`,
  `task_tracker` and `browser_tool_set`; with `enable_browser: false` the
  same request gets the first three only, although Chromium is still in
  place. The setting applies when a conversation is created: the earlier
  conversation keeps the browser in its stored agent. A materialized agent
  profile's resolved tools are the agent-profile family's view.
- **Browser disabled by config (`F27.browser-disabled`).** Same machine,
  `enable_browser: false`.
  ```sh
  control-agent-server restart --config-json '{"enable_browser": false}'
  control-agent-server api GET /api/tools/catalog --expect 200 --quiet \
    --check tools contains '{"name": "browser_tool_set", "user_selectable": true, "usable": false' \
    --save F27.browser-disabled/catalog
  control-agent-server api GET /api/tools/ --expect 200 --check . contains browser_tool_set
  test -L "$H/.cache/ms-playwright"
  ```
  The catalog forces `usable: false` although the probe of the previous
  bullet finds Chromium, while the raw registry still lists
  `browser_tool_set` (tools register at import, always with the browser).
- **Usable tools with the browser disabled (`F27.server-info-browser-disabled`), known bug.**
  Compare `/server_info` with the catalog on the same server.
  ```sh
  control-agent-server api GET /api/tools/catalog --expect 200 --quiet --check tools contains '{"name": "browser_tool_set", "user_selectable": true, "usable": false'
  control-agent-server api GET /server_info --auth none --expect 200 --check usable_tools contains terminal --check usable_tools not-contains browser_tool_set --save F27.server-info-browser-disabled/server-info  # bug
  ```
  Conversations on this server never get the browser, and the catalog says
  so; `usable_tools` should agree. Today it still lists `browser_tool_set`:
  `list_usable_tools()` runs only the Chromium probe and ignores
  `enable_browser`, which only the catalog route applies.
- **Browser-only sub-agent with the browser disabled (`F27.subagents-browser-disabled`), known bug.**
  List the built-ins on the same server.
  ```sh
  control-agent-server api GET /api/tools/catalog --expect 200 --quiet --check tools contains '{"name": "browser_tool_set", "user_selectable": true, "usable": false'
  control-agent-server api POST /api/sub-agents --json '{"load_user": false, "load_project": false}' --expect 200 --check agents contains '"name": "bash-runner"' --check agents not-contains '"name": "web-researcher"' --save F27.subagents-browser-disabled/builtins  # bug
  ```
  `discover_builtin_agents(enable_browser=False)` drops `web-researcher`,
  whose only tool is `browser_tool_set`, but the route calls it with the
  default `True` regardless of the server config, so today all four
  built-ins are listed and a client can offer a sub-agent that has no usable
  tool. The server also registers the built-in agents for conversations with
  `enable_browser=True` at import (`tool_router.py`), so by source the
  delegation registry that a conversation's task tool reads still holds
  `web-researcher` (not driven here).
- **Docker conversation runtime (`F27.catalog-docker-runtime`).** Remove the
  Chromium link and restart as an orchestrator whose conversations would run
  in containers; no container starts, so no Docker daemon is needed.
  ```sh
  rm "$H/.cache/ms-playwright"
  control-agent-server restart --reset-config \
    --config-json '{"conversation_runtime": "docker", "conversation_image": "ghcr.io/openhands/agent-server:1.2.3-python-minimal"}'
  control-agent-server api GET /api/tools/catalog --expect 200 --quiet \
    --check tools contains '{"name": "browser_tool_set", "user_selectable": true, "usable": false' \
    --save F27.catalog-docker-runtime/minimal-image
  control-agent-server restart --reset-config \
    --config-json '{"conversation_runtime": "docker", "conversation_image_has_browser": false}'
  control-agent-server api GET /api/tools/catalog --expect 200 --quiet \
    --check tools contains '{"name": "browser_tool_set", "user_selectable": true, "usable": false'
  control-agent-server restart --reset-config --config-json '{"conversation_runtime": "docker"}'
  control-agent-server api GET /server_info --auth none --expect 200 --check conversation_runtime eq docker
  control-agent-server api GET /api/tools/catalog --expect 200 --quiet \
    --check tools contains '{"name": "browser_tool_set", "user_selectable": true, "usable": true' \
    --check tools contains '{"name": "terminal", "user_selectable": true, "usable": true' \
    --save F27.catalog-docker-runtime/stock-image
  test ! -e "$H/.cache/ms-playwright"
  ```
  A `-minimal` tag of the stock image and `conversation_image_has_browser:
  false` make the browser unusable; the stock image (the default
  `conversation_image`) makes it usable although the server process has no
  Chromium (`F27.browser-probe` showed it unusable without the link). In this
  mode `/server_info` `usable_tools` still reports what the server process
  itself probes, so it leaves `browser_tool_set` out here: the same gap as
  `F27.server-info-browser-disabled`, not asserted because on a machine with
  a system Chromium the process probe finds it.

## Gotchas

- `POST /api/hooks` never reports an error: a missing, empty or broken
  `hooks.json` is the same `{"hook_config": null}`. Check the server log
  (`logs --grep 'Failed to load hooks'`) to tell them apart.
- Only `<project_dir>/.openhands/hooks.json` is read by the route. The SDK's
  `HookConfig.load()` also falls back to `$OH_PERSISTENCE_DIR/hooks.json`, but
  the route does not, and conversations load no hooks unless `hook_config`
  (or a plugin) brings them.
- Hook event keys are snake_case in responses (`pre_tool_use`, ...); PascalCase
  and the `{"hooks": {...}}` wrapper are accepted on disk. `HookExecutionEvent`
  reports the PascalCase name (`hook_event_type: PreToolUse`). The `async`
  flag is serialized as `async`.
- The sub-agents catalog validates frontmatter only. It does not resolve
  `skills`, `tools` or `model` (a profile name); those fail later, when a
  conversation registers the agents (`F27.subagents-bad-skill`) or spawns
  one. Plugin agents and programmatic registrations are not listed.
- `$OH_PERSISTENCE_DIR/agents` replaces `~/.openhands/agents` for user
  agents; in a launched run both are `<run>/home/.openhands/agents`, so the
  bullets cannot tell the two apart.
- `web-researcher`'s MCP `env` values come back masked (`**********`), even
  the `${TAVILY_API_KEY}` placeholder, because MCP env values are secrets.
- Catalog entries have no stable index (registration order), so the recipes
  match an entry's serialized prefix with `--check tools contains` or
  `matches`; field order is `name`, `user_selectable`, `usable`,
  `description`, `in_default_set`.
- Browser usability depends on the machine: a Chromium at a standard path
  (`/usr/bin/chromium`, `/usr/bin/google-chrome`, ...) or on the server's
  `PATH` is always found. On such a machine the negative control of
  `F27.browser-probe` fails, and the first check of
  `F27.browser-playwright-path` fails so that bullet stays an expected
  failure without proving the bug; run the family where Chromium lives only
  in a Playwright directory (`capabilities` names it). The probe is cached
  per process (`functools.cache`), so restart after changing what is
  installed. With `conversation_runtime: docker` nothing is probed and every
  tool is reported usable except as the image settings say.
- Client tools register process-wide on conversation start and disappear from
  `GET /api/tools/` after a restart until their conversation is loaded again;
  the catalog never shows them. A client tool named like a registered server
  tool (`terminal`, `file_editor`) makes `POST /api/conversations` a 422, and
  so does a name that another conversation in the same process registered
  with a different `parameters` schema (the generated action kind
  `ClientAction_<name>` is process-global); a restart clears that.
- The server acknowledges a client tool call itself (`Tool call dispatched to
  client.`) and the agent loop goes on; there is no route that posts the
  client's result back into that call.
- `blocked_actions` is consumed in the same agent step that the hook blocked,
  so after a run a GET shows `{}`; read the `ConversationStateUpdateEvent`
  with key `blocked_actions` (or the `UserRejectObservation`) instead.
  `blocked_messages` stays visible only while the blocked message waits for
  a run. A model may retry a blocked call; the recipes allow several blocked
  attempts (`--expect-min`).
- `tool_module_qualnames` changes process-wide registries, and a custom
  tool's events need its module to deserialize
  (`F27.custom-tool-missing-on-resume`). The server
  log wraps long lines, so the recipes grep for short phrases
  (`Extended sys.path with 1 directory`, `Failed to import module`).
- `OH_EXTRA_PYTHON_PATH` only extends `sys.path`; nothing is imported until a
  create names the module or `--import-modules` does at startup. A
  launched run gets the variable with `restart --env`, but `--import-modules`
  needs a server started outside `launch` (`F27.import-modules`).
- `GET /api/tools` and `/api/tools/catalog/` redirect (307) before
  authentication; `map check` validates recipe paths against the route table,
  so the recipes call the slash-less path through a shell variable.
