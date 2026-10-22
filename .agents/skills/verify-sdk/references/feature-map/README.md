# SDK feature map (in-process)

This directory maps what the OpenHands SDK does when a Python program uses
it directly, without the agent server: the agent loop, LLMs, local
conversations, events, context and condensers, skills, plugins, MCP, hooks,
sub-agents, the critic, security, tools, workspace backends, settings,
observability and the ACP agent. It is the in-process companion to the
agent-server map
([`verify-agent-server`](../../../verify-agent-server/references/feature-map/README.md))
and follows its layout. Family IDs here are `Snn`, so they never collide with
that map's `Fnn`.

Maintenance baseline: main@4cc058275c5a08d845bd846576aafc80e5dd7d0d (2026-10-11). The first mapping; the next maintenance pass starts from this commit, a pass proposes the next baseline in its PR, and merging that PR accepts it.

The map has **458 sub-features**, **29 of them untested** (no unit or cross
test exercises the behavior; most of those have an example script).

## What each sub-feature links

Every sub-feature bullet ends with three links:

- `Unit:` the unit or cross tests that cover it with a deterministic LLM
  (`openhands.sdk.testing.TestLLM` or mocks). These run on every PR; the map
  links them for reference and never reruns them. `**none (untested)**` means
  a search of `tests/` found nothing.
- `Example:` the example script that shows it with a real model. Examples run
  only on the `test-examples` label (`run-examples.yml`), and the ones in
  `_EXCLUDED_EXAMPLES` (`tests/examples/test_examples.py`) never run in CI.
- `Real-LLM:` the integration test (`tests/integration/tests/`: t01-t10 tasks,
  b01-b05 behavior, c01-c05 condenser, a01-a08 API compliance), which run
  only on labels or dispatch.

So a row with a `Unit:` link is checked on every PR, and a row with only an
`Example:` or `Real-LLM:` link is checked only when someone adds a label. The
map's own recipes are a third, independent proof: programs that use the SDK
the way a user's program does (see [recipes.md](../recipes.md)).

## Baseline preconditions

- `uv sync --dev` has run in the repository root.
- Live bullets: `DEEPSEEK_API_KEY` is exported. `control-sdk new --llm
  deepseek-flash` runs use it, and `control-sdk exec --model-env` hands it to
  examples and the integration runner as `LLM_API_KEY`/`LLM_MODEL`.
- Run commands from the repository root.
- `control-sdk` and `sdk-map` are on `PATH`:
  `export PATH="$PWD/.agents/skills/verify-sdk/scripts:$PATH"`.
  `sdk-map run --file Snn` replays a family's `sh` blocks into a ledger.

## Family entry contract

Each family file starts with an H1 title, one paragraph describing the
behavior as an SDK user sees it, a `Source:` line, an optional `Needs:` line
(`llm`, `tmux`, `git`, `node`, `uvx`, `docker`, `apptainer`, `chromium`,
`network`, `cloud`, `k8s`) and an `Examples:` line. Then exactly four H2
sections: `Sub-features`, `How to get to it (agent POV)`, `Driving it`
(starting with `Preconditions:`), and `Gotchas`. This is the #5621 contract
with `Routes:` replaced by `Examples:` and `Driving it with
control-agent-server` replaced by `Driving it`, because there is no server to
launch: the `sh` blocks drive the SDK in-process with `control-sdk` (a run,
persisted conversations, programs under `programs/`), and `sdk-map run`
replays them. A block never calls `pytest`. Each `Driving it` bullet names the
sub-feature IDs it proves in its bold label (`Snn.prefix-*` covers every ID
with that prefix), and a label that says "blocked" is reported, not run;
`sdk-map check` fails when a sub-feature has no bullet or a cited path is
gone, or a `control-sdk` call does not parse or names a missing program.

Bullets that need Docker, Apptainer, a cloud account, a Kubernetes cluster or
an interactive login are labeled **blocked**.

## Families

| ID | Family | What it covers | Entry points | Needs | Sub-features | Untested |
|---|---|---|---|---|---|---|
| S01 | [Agent step loop](S01-agent-loop.md) | step handling (tool calls, text, empty replies), iteration and budget limits, stuck detector, parallel tools, messages sent mid-run, image input, `ask_agent`, structured output, recovery from bad tool calls and context overflow | `Conversation(max_iteration_per_run=, stuck_detection=, max_budget_per_run=)`, `Agent(tool_concurrency_limit=)`, `ask_agent()`, `response_schema` | llm, tmux, network | 24 | 1 |
| S02 | [LLM core](S02-llm-core.md) | completion, responses and `generate`; streaming; reasoning and thinking blocks; native and prompted tool calling; metrics, stats and pricing; retries and error mapping; caching, token counts, image inlining, completion logs | `LLM(...)`, `llm.completion`/`responses`, `Conversation(token_callbacks=)`, `llm.metrics` | llm, network | 25 | 0 |
| S03 | [LLM registry, routing, fallback, profiles and model switching](S03-llm-routing-profiles.md) | `LLMRegistry`, multimodal and random routers, `FallbackStrategy`, `LLMProfileStore`, `switch_profile`/`switch_llm`, the switch-LLM tool, `route_task_to_model`, subscription login | `LLMRegistry`, `MultimodalRouter`, `FallbackStrategy`, `LLMProfileStore`, `LLM.subscription_login()` | llm, network | 24 | 3 |
| S04 | [Local conversation lifecycle](S04-local-conversation.md) | factory and ids, persistence and resume, callbacks, close, pause, `arun` and interrupt, secrets, visualizers, fork and branches, goal loop, final response, stats, title and tags | `Conversation(persistence_dir=, callbacks=, visualizer=, secrets=)`, `run`/`pause`/`arun`/`interrupt`/`fork`/`run_goal` | llm, tmux, network, uvx | 25 | 0 |
| S05 | [Events and the event log](S05-events.md) | event model, JSON round trip, conversion to LLM messages and OpenAI formats, unmatched actions, the `EventLog`, error classification, completion and token events | `Event.model_validate_json`, `events_to_messages`, `conversation.state.events` | llm, tmux | 18 | 1 |
| S06 | [Agent context, system prompt, condensers and memory](S06-context-condensers.md) | `AgentContext`, system prompt sections and vendor files, persona, persistent memory, the condenser `View`, `LLMSummarizingCondenser`, `condense()`, overflow condensation, no-op and pipeline condensers | `Agent(agent_context=, system_prompt=, condenser=)`, `AgentContext(load_memory=)`, `conversation.condense()` | llm, tmux | 23 | 1 |
| S07 | [Skills](S07-skills.md) | skill sources (programmatic, Markdown, AGENTS.md, SKILL.md, user, project, public, installed), keyword, task and path triggers, `<available_skills>`, `invoke_skill`, inline commands, disabling, install and marketplace | `AgentContext(skills=, load_*_skills=, disabled_skills=)`, `.agents/skills/`, `install_skill` | llm, git, network | 24 | 0 |
| S08 | [Plugins, marketplaces and the extension store](S08-plugins-marketplace.md) | both plugin layouts, path containment, commands as skills, merging into the agent, `resolved_plugins`, fetch cache, installed and ambient plugins, marketplace registry, `load_plugin()` | `Conversation(plugins=[PluginSource(...)])`, `Plugin.load`, `conversation.load_plugin()` | llm, git, network, uvx | 20 | 0 |
| S09 | [MCP client and tools](S09-mcp.md) | stdio, HTTP and SSE servers, auth and OAuth, enabled flag, timeouts, failing servers, schema conversion, observations, secrets in calls, reconnection, `list_changed`, tool filtering | `Agent(mcp_config=, filter_tools_regex=)`, `create_mcp_tools`, `MCPOAuthAuthentication` | llm, uvx, node, network | 19 | 0 |
| S10 | [Hooks](S10-hooks.md) | config format and discovery, matchers, command hooks, blocking tool calls and prompts, context injection, Stop, SessionStart and SessionEnd, `HookExecutionEvent`, prompt and agent hooks, plugin hooks | `Conversation(hook_config=HookConfig(...))`, `HookDefinition(type=)`, `HookManager` | llm, git | 20 | 1 |
| S11 | [Sub-agents and delegation](S11-subagents-delegation.md) | the `task` tool lifecycle, agent registry, Markdown agent files and `AgentDefinition`, built-in agents, `SubAgentScope`, the legacy delegate executor | `Tool(name=TaskToolSet.name)`, `register_agent`, `.agents/agents/*.md` | llm | 23 | 2 |
| S12 | [Critic and critic-driven refinement](S12-critic.md) | `CriticResult`, rule critics, evaluation modes, iterative refinement, `APIBasedCritic`, critic from settings | `Agent(critic=APIBasedCritic(...))`, `event.critic_result` | llm, network | 15 | 4 |
| S13 | [Security analyzers, confirmation mode and secrets](S13-security-confirmation.md) | risk levels, confirmation policies, approve and reject, LLM, pattern, policy-rail, ensemble, ToolShield and GraySwan analyzers, secret sources, env injection, masking | `set_confirmation_policy`, `set_security_analyzer`, `Conversation(secrets=)` | llm, network | 21 | 0 |
| S14 | [Core tools and tool presets](S14-core-tools.md) | terminal (tmux and subprocess), file editor, apply_patch, Gemini file tools, glob, grep, output masking, default, GPT-5 and Gemini presets | `Tool(name="terminal" \| "file_editor" \| ...)`, `get_default_agent`, `get_gpt5_*`, `get_gemini_*` | llm, tmux, git, network | 24 | 0 |
| S15 | [Browser tools and session recording](S15-browser.md) | `BrowserToolSet`, Chromium detection and fallback, navigation, interaction, tabs, storage, screenshots, rrweb recording | `Tool(name="browser_tool_set")`, `BrowserToolExecutor` | chromium, llm, network | 17 | 0 |
| S16 | [Planning, task tracking, oracle, ToM and workflow tools](S16-planning-workflow-tools.md) | task tracker, planning preset and PLAN.md editor, plan-then-execute, `ask_oracle`, `tom_consult`, sleeptime compute, the workflow tool | `Tool(name="task_tracker" \| "ask_oracle" \| "tom_consult" \| "workflow_tool_set")`, `get_planning_agent` | llm | 20 | 6 |
| S17 | [Custom tools, client-defined tools and the tool registry](S17-custom-and-client-tools.md) | custom tool classes and tool sets, registration and naming, tool schemas, injected fields, catalog seal, module qualnames, client tools, `execute_tool` | `register_tool`, `Tool(name, params)`, `Conversation(client_tools=)`, `execute_tool` | llm | 23 | 0 |
| S18 | [Workspaces and sandbox backends](S18-workspace-backends.md) | the `Workspace` factory, `LocalWorkspace`, `RemoteWorkspace`, and the Docker, API-remote, Cloud, Apptainer and agent-sandbox backends (start, health, commands, files, pause, cleanup) | `Workspace(...)`, `LocalWorkspace`, `openhands.workspace.*Workspace` | docker, apptainer, cloud, k8s, network, llm, git | 24 | 3 |
| S19 | [Agent settings, persisted schema migrations and agent profiles](S19-settings.md) | `OpenHandsAgentSettings`, ACP settings, schema migrations v0 to v8, settings diffs and export, the persisted-settings compatibility gate, agent profiles and their store | `openhands.sdk.settings`, `openhands.sdk.profiles`, `~/.openhands/agent-profiles/` | llm, tmux, network | 25 | 0 |
| S20 | [Observability, tracing and logging](S20-observability.md) | enabling tracing, Laminar and OTLP backends, conversation, step, tool and LLM spans, sub-agent trace links, automation helpers, the logger | `LMNR_*`/`OTEL_*` env vars, `Conversation(observability_*=)`, `get_logger` | llm, network | 20 | 5 |
| S21 | [ACP agent (in-process)](S21-acp-agent.md) | construction, provider registry, handshake, turns and tool-call events, permission auto-approval, timeouts and cancel, session resume, model switching, usage, MCP forwarding, secrets, cleanup | `ACPAgent(acp_command=...)`, `switch_acp_model`, `ACPAgentSettings` | node, network, llm | 24 | 2 |

## Not mapped

Left out of this map, with the reason:

- The agent server, `RemoteConversation`, and `examples/02_remote_agent_server/`
  as a client: owned by the agent-server map, mostly F33. S18 maps
  only the SDK-side workspace classes those examples construct.
- `examples/03_github_workflows/`: GitHub Actions recipes built on the SDK,
  not SDK features; they need a repository and Actions to run.
- `clients/typescript`: owned by the agent-server map's F34.
- `openhands-sdk/openhands/sdk/launch/` (two-stage profile resolve and
  finalize) and `openhands-sdk/openhands/sdk/git/` (git change and diff
  helpers): reached here only indirectly, through S19 profiles and S18
  `git_changes`. Each could become its own family.
- `openhands-sdk/openhands/sdk/io/` (file stores) and
  `openhands-sdk/openhands/sdk/utils/`: internals reached through S04
  persistence and S13 masking, not features an SDK user calls directly.
