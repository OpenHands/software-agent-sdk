# Sub-agents and delegation

A Python program gives its main `Agent` the task tool (`Tool(name=TaskToolSet.name)`) and registers sub-agent types; the model then calls `task` with a prompt and a `subagent_type`, and the SDK runs a separate, blocking `LocalConversation` for that sub-agent in the same workspace and returns its final answer as a `TaskObservation`. Sub-agent types come from four sources, first registration wins by name: programmatic `register_agent(...)`, plugin agents, Markdown files under `.agents/agents/` or `.openhands/agents/` (project, then user home), and the built-in Markdown agents shipped in `openhands-tools` (`general-purpose`, `code-explorer`, `bash-runner`, `web-researcher`) that `register_builtins_agents()` adds. A task can be resumed by its `task_xxxxxxxx` id, its metrics roll up into the parent's `conversation_stats`, its iteration and budget limits and confirmation policy come from the definition or the parent, and a `SubAgentScope` can forbid sub-agents from using tools or MCP servers the parent lacks.

Source: `openhands-sdk/openhands/sdk/subagent/`, `openhands-tools/openhands/tools/task/`, `openhands-tools/openhands/tools/delegate/`, `openhands-tools/openhands/tools/preset/default.py`, `openhands-tools/openhands/tools/preset/subagents/`, `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`

Needs: `llm`

Examples: `examples/01_standalone_sdk/25_agent_delegation.py`, `examples/01_standalone_sdk/41_task_tool_set.py`, `examples/01_standalone_sdk/42_file_based_subagents.py`

## Sub-features

- `S11.register-programmatic`: `register_agent(name, factory_func, description)` adds a type that `get_agent_factory(name)` returns; a duplicate name raises, and an unknown name raises `ValueError("Unknown agent ...")`. Unit: `tests/sdk/subagent/test_subagent_registry.py::test_register_and_retrieve_custom_agent_factory`, `::test_unknown_agent_type_raises_value_error`. Example: `examples/01_standalone_sdk/25_agent_delegation.py`. Real-LLM: none.
- `S11.register-if-absent`: `register_agent_if_absent` keeps an existing registration and returns False, so plugin, file and built-in sources never overwrite programmatic ones. Unit: `tests/sdk/subagent/test_subagent_registry.py::test_register_agent_if_absent_existing`, `::test_register_file_agents_skips_programmatic`. Example: none. Real-LLM: none.
- `S11.markdown-schema`: `AgentDefinition.load` parses YAML frontmatter (`name`, `description`, `tools`, `skills`, `model`, `color`, `max_iteration_per_run`, `max_budget_per_run`, `hooks`, `profile_store_dir`, `mcp_config`, `permission_mode`, `condenser`); the body becomes the system prompt, `<example>` tags become `when_to_use_examples`, unknown keys land in `metadata`, invalid values raise. Unit: `tests/sdk/subagent/test_subagent_schema.py`. Example: none. Real-LLM: none.
- `S11.file-discovery`: `load_project_agents` / `load_user_agents` scan only top-level `*.md` in `.agents/agents/` then `.openhands/agents/`, skip `README.md`, and `.agents` wins over `.openhands` for one name; `discover_agents` reports level and source without touching the registry. Unit: `tests/sdk/subagent/test_subagent_loader.py`, `tests/sdk/subagent/test_discover_agents.py`. Example: none. Real-LLM: none.
- `S11.file-precedence`: `register_file_agents(work_dir)` registers project agents before user agents, so project wins by name. Unit: `tests/sdk/subagent/test_subagent_registry.py::test_register_file_agents_project_priority`. Example: none. Real-LLM: none.
- `S11.conversation-autoregister`: the first `send_message()`/`run()` of a `LocalConversation` calls `register_file_agents(workspace.working_dir)`, so Markdown agents in the workspace become task types without any explicit call. Unit: **none (untested)** (only `register_file_agents` itself and plugin agents, `tests/sdk/conversation/test_local_conversation_agent_plugins.py::TestClientExtension::test_agents_are_registered`, are tested). Example: none. Real-LLM: none.
- `S11.plugin-agents`: `register_plugin_agents` registers `Plugin.agents` with their hooks, skipping existing names. Unit: `tests/sdk/subagent/test_subagent_registry.py::test_register_plugin_agents`, `::test_register_plugin_agents_with_hooks`. Example: none. Real-LLM: none.
- `S11.definition-factory`: `agent_definition_to_factory(defn)` builds an `Agent` with the definition's tools (unknown tool raises), skills (project over user, unknown raises), system prompt as `system_message_suffix`, MCP config, and a default summarizing condenser unless `condenser: none`. Unit: `tests/sdk/subagent/test_subagent_registry.py` (`test_agent_definition_to_factory_*`, `test_factory_*condenser*`). Example: `examples/01_standalone_sdk/42_file_based_subagents.py`. Real-LLM: none.
- `S11.definition-model`: `model: inherit` reuses the parent LLM; another value loads that LLM profile from `profile_store_dir` or the default store, and a missing profile raises. Unit: `tests/sdk/subagent/test_subagent_registry.py::test_agent_definition_to_factory_model_profile`, `::test_agent_definition_to_factory_model_profile_not_found`, `::test_agent_definition_to_factory_profile_store_dir`. Example: none. Real-LLM: none.
- `S11.builtin-agents`: `register_builtins_agents(enable_browser=True)` registers the four built-in Markdown agents as fallbacks, leaves out `web-researcher` when `enable_browser=False`, and keeps deprecated names (`default`, `default cli mode`, `explore`, `bash`) working with a `DeprecatedWarning`. Unit: `tests/tools/test_builtin_agents.py`. Example: `examples/01_standalone_sdk/25_agent_delegation.py`. Real-LLM: none.
- `S11.factory-info`: the `task` tool description lists the registered types (`get_factory_info`) and includes usage examples only for registered built-ins. Unit: `tests/sdk/subagent/test_subagent_registry.py::test_factory_info`, `tests/tools/task/test_task_tool_set.py::TestTaskToolExamples`. Example: none. Real-LLM: none.
- `S11.task-run`: a `task` call starts a fresh sub-conversation, sends the prompt (with the parent visualizer name as sender), runs it to completion and returns its final response with `status` `completed`; ids are sequential `task_00000001`, ... and unique under concurrency. Unit: `tests/tools/task/test_task_tool_set.py::TestTaskToolSetIntegration::test_basic_task_delegation_and_result`, `tests/tools/task/test_task_manager_thread_safety.py`. Example: `examples/01_standalone_sdk/41_task_tool_set.py`. Real-LLM: none.
- `S11.task-resume`: `task(resume="task_...")` reopens the same sub-conversation id from the persistence dir, so the sub-agent keeps its earlier context; an unknown id returns an error observation listing the known ids. Unit: `tests/tools/task/test_task_tool_set.py::TestTaskToolSetIntegration::test_task_resume_across_turns`, `::test_resume_nonexistent_task_returns_error`, `tests/tools/task/test_task_manager.py::TestTaskManager::test_resume_after_evict`. Example: `examples/01_standalone_sdk/41_task_tool_set.py`. Real-LLM: none.
- `S11.task-errors`: an unknown `subagent_type`, an exception in the sub-agent, or a non-FINISHED stop (iteration limit, stuck, paused) comes back as an error observation with the last `ConversationErrorEvent` detail and any partial output, never as an empty success. Unit: `tests/tools/task/test_task_tool_set.py::TestTaskToolSetIntegration::test_unknown_agent_type_returns_error_observation`, `::test_sub_agent_exception_returns_error_observation`, `tests/tools/task/test_task_manager.py::TestRunTask::test_non_finished_status_surfaced_as_error`, `::TestRunErrorSurfacing`. Example: none. Real-LLM: none.
- `S11.task-limits`: the sub-conversation's `max_iteration_per_run` and `max_budget_per_run` come from the definition, else from the parent conversation. Unit: `tests/tools/task/test_task_manager.py::TestTaskManager::test_create_task_prefers_factory_max_iteration_over_parent`, `::TestTaskManagerBudget`. Example: none. Real-LLM: none.
- `S11.task-metrics`: each sub-agent gets its own LLM copy (streaming off, fresh metrics), and after a run its combined metrics are stored in the parent's `conversation_stats.usage_to_metrics["task:<id>"]`, replaced on resume, not double counted. Unit: `tests/tools/task/test_task_manager.py::TestTaskMetrics`. Example: `examples/01_standalone_sdk/25_agent_delegation.py` (prints `EXAMPLE_COST`). Real-LLM: none.
- `S11.task-persistence`: with a persisting parent, sub-conversations are stored under `<parent persistence_dir>/subagents/` and kept on close; without one they go to a temporary `openhands_tasks_*` dir that `close()` deletes. Unit: `tests/tools/task/test_task_manager.py::TestTaskManagerPersistence`. Example: none. Real-LLM: none.
- `S11.task-hooks`: a definition's `hooks` become the sub-conversation's `hook_config`, on create and on resume. Unit: `tests/tools/task/test_task_manager.py::TestTaskManagerHooks`. Example: none. Real-LLM: none.
- `S11.task-observability`: sub-conversations share the parent's prompt cache key and carry delegate trace metadata (`is_delegate`, `task_id`, `subagent_type`, `parent_session_id`, parent span link) and the `delegate` tag. Unit: `tests/tools/task/test_task_manager.py::TestTaskManager::test_sub_agents_inherit_parent_prompt_cache_key`, `::test_marks_conversation_as_delegate_with_linking_metadata`, `::test_delegate_metadata_includes_parent_span_link`. Example: none. Real-LLM: none.
- `S11.task-confirmation`: a definition's `permission_mode` sets the sub-conversation's confirmation policy, omission inherits the parent's; when the sub-agent waits for confirmation, `TaskToolSet.create(confirmation_handler=...)` decides, and without a handler the actions are approved. Unit: **none (untested)**. Example: none. Real-LLM: none.
- `S11.sub-agent-scope`: `SubAgentScope(tools=True, mcp_servers=True)` (the `sub_agent_scope` tool param) hides and refuses sub-agents whose tools or MCP servers the parent lacks, compares built-in tool names canonically, propagates to nested delegation tools, and says so when no sub-agent fits. Unit: `tests/sdk/subagent/test_subagent_scope.py`, `tests/tools/task/test_sub_agent_scope.py`. Example: none. Real-LLM: none.
- `S11.delegate-executor`: the older `DelegateExecutor` (`spawn` named sub-agents, then `delegate` a dict of tasks run in parallel threads) closes replaced and spawned sub-agents, rolls back a partial spawn, merges metrics, and inherits the parent's persistence dir. Unit: `tests/tools/delegate/test_delegation.py`. Example: none. Real-LLM: none.
- `S11.delegation-visualizer`: `DelegationVisualizer(name=...)` labels messages by sender and agent name and creates per-task sub-visualizers. Unit: `tests/tools/delegate/test_visualizer.py`. Example: all three examples. Real-LLM: none.

## How to get to it (agent POV)

- `Agent(tools=[Tool(name=TaskToolSet.name)])` (`openhands.tools.task`; importing the module registers `task_tool_set` and `task`). Tool params: `Tool(name="task_tool_set", params={"sub_agent_scope": {"tools": True, "mcp_servers": True}})`. Model-facing action: `TaskAction(prompt, subagent_type="general-purpose", resume=None, description=None)`. Covers `S11.task-*`, `S11.sub-agent-scope`, `S11.factory-info`.
- `TaskToolSet.create(conv_state, confirmation_handler=..., sub_agent_scope=...)` when building tools by hand (`S11.task-confirmation`).
- `openhands.sdk.subagent`: `register_agent`, `register_agent_if_absent`, `register_file_agents`, `register_plugin_agents`, `get_agent_factory`, `get_factory_info`, `discover_agents`, `load_project_agents`, `load_user_agents`, `AgentDefinition`, `agent_definition_to_factory`, `SubAgentScope` (also re-exported: `from openhands.sdk import register_agent, agent_definition_to_factory`). Covers `S11.register-*`, `S11.markdown-schema`, `S11.file-*`, `S11.definition-*`, `S11.plugin-agents`.
- `openhands.tools.register_builtins_agents(enable_browser=True)`; definitions in `openhands-tools/openhands/tools/preset/subagents/*.md` (`S11.builtin-agents`).
- Files on disk: `{workspace}/.agents/agents/*.md`, `{workspace}/.openhands/agents/*.md`, `~/.agents/agents/*.md`, `~/.openhands/agents/*.md`, picked up on the conversation's first `send_message()`/`run()` (`S11.conversation-autoregister`).
- `Agent(tool_concurrency_limit=2)` lets two `task` calls in one model response run in parallel (example 25).
- `openhands.tools.delegate`: `DelegateExecutor(max_children=5, confirmation_handler=...)`, `DelegationVisualizer` (`S11.delegate-executor`, `S11.delegation-visualizer`).

## Driving it

Preconditions:
- `uv sync --dev` has run; `control-sdk` is on `PATH` (`export PATH="$PWD/.agents/skills/verify-sdk/scripts:$PATH"`). `sdk-map run` does both.
- `tmux` on `PATH` (sub-agents use `TerminalTool`).
- Live bullets: `DEEPSEEK_API_KEY` exported.

Programs give each programmatic sub-agent its own scripted LLM (`programs/S11/s11_helpers.py` records every call); Markdown and built-in sub-agents get a copy of the parent's LLM, and copies of one `TestLLM` share its script queue, so a single script interleaves parent and sub-agent turns.

- **The task tool's lifecycle (`S11.register-programmatic`, `S11.task-run`, `S11.task-resume`, `S11.task-errors`, `S11.task-limits`, `S11.task-metrics`, `S11.task-persistence`, `S11.task-hooks`).** A persisted parent with `TaskToolSet` delegates to `echoer` (which runs `echo hello` and has a `PostToolUse` hook in its definition), resumes `task_00000001`, asks for an unknown type and an unknown task id, runs `looper` (definition limit 1) and `crasher` (its LLM raises); a second parent with `max_iteration_per_run=2` delegates to `plodder`, which has no limit of its own.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S11/task_lifecycle \
    --check execution_status eq finished \
    --check first_task.task_id eq task_00000001 --check first_task.status eq completed --check first_task.text eq "said hello" \
    --check hello_written eq true \
    --check resume_task.task_id eq task_00000001 --check resume_task.text eq "you asked me to say hello" \
    --check resume_saw_first_prompt eq true \
    --check unknown_type.is_error eq true --check unknown_type.text contains "Unknown agent 'nobody'" \
    --check unknown_resume.is_error eq true --check unknown_resume.text contains "Available tasks: task_00000001" \
    --check looper.task_id eq task_00000002 --check looper.is_error eq true \
    --check looper.text contains "maximum iterations limit" --check looper_llm_calls eq 1 \
    --check plodder.is_error eq true --check plodder.text contains "maximum iterations limit" --check plodder_llm_calls eq 2 \
    --check crasher.is_error eq true --check crasher.text contains "bad key" \
    --check usage_ids contains task:task_00000001 --check usage_ids contains task:task_00000003 --check usage_ids len-eq 3 \
    --check subagent_dirs eq 3 --check sub_hook_lines len-eq 1 --check sub_hook_lines.0 eq sub-hook
  ```
  The resumed sub-agent's LLM request still holds the first prompt; a non-finished sub-agent comes back as an error observation with the run's error, never an empty success; three sub-conversations stay under `<parent>/subagents/` after close.
- **Delegates share the parent's prompt-cache key (`S11.task-observability`).** Same scenario; each `echoer` LLM call records its call context.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S11/task_lifecycle \
    --check sub_cache_key_is_parent.0 eq true --check sub_cache_key_is_parent.1 eq true \
    --check sub_cache_key_is_parent.2 eq true --check sub_session_differs eq true
  ```
  Expected: every sub-agent call carries the parent conversation's id as `prompt_cache_key`, on create and on resume. Known failure (reproduced 2026-10-13, not yet filed): the two calls of the created task carry the parent's key, but the call made after `task(resume=...)` carries the sub-conversation's own id (`sub_cache_key_is_parent` is `[true, true, false]`): `TaskManager._resume_task` builds the `LocalConversation` without `prompt_cache_key=...`, unlike `_get_conversation`.
- **Delegate trace metadata (`S11.task-observability`), blocked.** `is_delegate`, `task_id`, `subagent_type`, `parent_session_id`, the parent span link and the `delegate` tag go only to the observability backend; proving them needs a Laminar/OpenTelemetry collector to read spans from.
- **Registry, Markdown definitions and factories (`S11.register-programmatic`, `S11.register-if-absent`, `S11.markdown-schema`, `S11.file-discovery`, `S11.file-precedence`, `S11.plugin-agents`, `S11.definition-factory`, `S11.definition-model`).** The program lays out agent files in the workspace (`.agents/agents/`, `.openhands/agents/`, a `README.md`, a nested dir), the user's home (`~/.agents/agents/`) and `$OH_PERSISTENCE_DIR/agents/`, saves an LLM profile, and goes through `openhands.sdk.subagent`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S11/registry \
    --check schema.tools len-eq 2 --check schema.skills.0 eq house-style --check schema.color eq blue \
    --check schema.max_iteration_per_run eq 7 --check schema.max_budget_per_run eq 0.5 \
    --check schema.permission_mode eq always_confirm --check schema.condenser eq NoOpCondenser \
    --check schema.hooks_post_tool_use eq 1 --check schema.mcp_servers.0 eq fetch \
    --check schema.system_prompt eq "You are a careful reviewer." --check schema.examples len-eq 2 \
    --check schema.metadata.team eq platform --check invalid_permission_mode contains "Invalid permission_mode" \
    --check project_names len-eq 3 --check project_names not-contains deep --check project_names not-contains README \
    --check discovered.reviewer.1 contains "Reviews code" --check discovered.shared.1 eq "project copy" \
    --check discovered.user-only.0 eq user --check discovered.late.0 eq user \
    --check registered_before_files len-eq 0 \
    --check duplicate_error contains "already registered" --check unknown_error contains "Unknown agent 'nobody'" \
    --check if_absent_returned eq false --check alpha_description eq "first alpha" \
    --check file_registered len-eq 4 --check file_registered not-contains shared \
    --check shared_description eq "programmatic shared" --check reviewer_from eq /.agents/agents/ \
    --check plugin_registered len-eq 1 --check plugin_registered.0 eq plug --check plug_hooks_stop eq 1 \
    --check factory.tools.0 eq terminal --check factory.suffix eq "You are a careful reviewer." \
    --check factory.skills.0 eq house-style --check factory.mcp_servers.0 eq fetch \
    --check factory.condenser eq NoOpCondenser --check factory.default_condenser eq LLMSummarizingCondenser \
    --check factory.default_condenser_usage_id eq condenser \
    --check unknown_tool_error contains "no_such_tool" --check unknown_skill_error contains "no-such-skill" \
    --check profile_model eq openai/gpt-4o-mini --check inherit_same_llm eq true \
    --check missing_profile_error contains "Profile nope not found"
  ```
- **Built-in agents and the task tool's type list (`S11.builtin-agents`, `S11.factory-info`).** `register_builtins_agents(enable_browser=False)`, then `True`; the deprecated names; the description `TaskToolSet.create(...)` gives the model.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S11/builtins \
    --check registered_without_browser len-eq 3 --check registered_without_browser not-contains web-researcher \
    --check registered_later_with_browser.0 eq web-researcher --check registered len-eq 4 \
    --check deprecated_maps_to.default eq general-purpose --check deprecated_maps_to.explore eq code-explorer \
    --check deprecated_maps_to.bash eq bash-runner --check deprecated_warned.bash eq true \
    --check description_lists.general-purpose eq true --check description_lists.web-researcher eq true \
    --check example_in_description.code-explorer eq true --check example_in_description.bash-runner eq true
  ```
- **A usage example for every registered built-in (`S11.factory-info`).** Same program.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S11/builtins \
    --check example_in_description.general-purpose eq true --check example_in_description.web-researcher eq true
  ```
  Known failure (reproduced 2026-10-13, not yet filed): with all four built-ins registered, only the `code-explorer` and `bash-runner` examples appear. `TASK_TOOL_EXAMPLES` keys the other two as `"general purpose"` and `"web researcher"` (spaces), while the built-ins register as `general-purpose` and `web-researcher`, so their examples never match.
- **Workspace Markdown agents picked up by the conversation (`S11.conversation-autoregister`, `S11.builtin-agents`, `S11.task-persistence`).** The workspace holds `.agents/agents/ws-reviewer.md`; the program never calls `register_file_agents`. A non-persisted parent delegates to it and to the built-in `bash-runner`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S11/workspace_agents \
    --check execution_status eq finished --check registered_before_send eq false --check registered_after_send eq true \
    --check observations.0.subagent eq ws-reviewer --check observations.0.text eq "notes say hi" \
    --check observations.1.subagent eq bash-runner --check observations.1.status eq completed \
    --check builtin_written eq true --check description_lists_ws_reviewer eq true --check script_left eq 0 \
    --check temp_task_dirs_before_close eq 1 --check temp_task_dirs_after_close eq 0
  ```
- **Sub-agent confirmation (`S11.task-confirmation`).** The program registers its own task tool set whose `create()` passes a `confirmation_handler`; `careful` has `permission_mode: always_confirm` and the handler rejects it; after `set_confirmation_policy(AlwaysConfirm())` on the parent, `inheritor` (no `permission_mode`) inherits the policy and the handler approves; a plain `TaskToolSet` without a handler approves on its own.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S11/task_confirmation \
    --check handler_calls len-eq 2 \
    --check handler_calls.0.task_id eq task_00000001 --check handler_calls.0.tools.0 eq terminal --check handler_calls.0.approved eq false \
    --check careful_file eq false --check observations.0.text eq "careful done" \
    --check parent_waited eq waiting_for_confirmation \
    --check handler_calls.1.task_id eq task_00000002 --check handler_calls.1.approved eq true \
    --check inherited_file eq true --check execution_status eq finished \
    --check auto_file eq true --check plain_observations.0.status eq completed
  ```
- **Sub-agent scope (`S11.sub-agent-scope`).** A parent with only the terminal sets `sub_agent_scope: {tools: true}` on its task tool; `editor` needs `file_editor`, `sheller` needs the terminal and has its own task tool; a parent with no tools of its own is offered nothing.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S11/scope \
    --check execution_status eq finished --check lists_sheller eq true --check lists_editor eq false \
    --check observations.0.is_error eq true --check observations.0.text contains "uses file_editor, which this agent does not have" \
    --check observations.1.status eq completed --check scoped_written eq true \
    --check nested_task_tool_scoped eq true --check lonely_says_none eq true
  ```
- **DelegateExecutor and DelegationVisualizer (`S11.delegate-executor`, `S11.delegation-visualizer`).** No tool is registered for `DelegateExecutor`, so the program wraps it in a small `delegate` tool, the way a program would; two typed workers (each with its own scripted LLM) run in parallel threads; then too many spawns, a spawn with an unknown type, and a delegate to an id that was never spawned. The parent's `DelegationVisualizer(name="Lead")` output is captured.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S11/delegate \
    --check execution_status eq finished \
    --check observations.0.text contains "Successfully spawned 2 sub-agents" \
    --check observations.1.text contains "Agent lodging_expert: lodging wrote lodging.txt" \
    --check observations.1.text contains "Agent activity_planner: activities wrote activities.txt" \
    --check files len-eq 2 \
    --check observations.2.is_error eq true --check observations.2.text contains "maximum is 5" \
    --check observations.3.is_error eq true --check observations.3.text contains "no_such_type" \
    --check observations.4.text contains "sub-agents not found: p" \
    --check usage_ids contains delegate:lodging_expert --check usage_ids contains delegate:activity_planner \
    --check subagent_dirs eq 2 \
    --check visualizer.lead_titles eq true --check visualizer.sub_agent_titles eq true --check visualizer.sender_titles eq true
  ```
  The failed `p, q` spawn leaves nothing behind: `p` is unknown afterwards.
- **Quiz with resume, example 41 (`S11.task-run`, `S11.task-resume`, `S11.delegation-visualizer`).** Live.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output "EXAMPLE_COST" -- python examples/01_standalone_sdk/41_task_tool_set.py
  ```
  The quiz host calls `task` twice, the second time with `resume`.
- **Programmatic plus built-in types in parallel, example 25 (`S11.register-programmatic`, `S11.builtin-agents`, `S11.task-metrics`).** Live.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output "EXAMPLE_COST" -- python examples/01_standalone_sdk/25_agent_delegation.py
  ```
  Two `task` calls run in parallel, a `bash-runner` sub-agent writes `london_trip_report.txt` into the run's workspace, and `EXAMPLE_COST` includes sub-agent spend.
- **Inline `AgentDefinition`, example 42 (`S11.definition-factory`).** Live.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output "EXAMPLE_COST" -- python examples/01_standalone_sdk/42_file_based_subagents.py
  ```
  The orchestrator delegates to `grammar-checker`, which reads `README.md` with `file_editor`.

## Gotchas

- Example 42's file name says "file based" but it defines the agent inline with `AgentDefinition`; no example loads `.agents/agents/*.md` from disk.
- The registry is a process-wide global. Tests reset it through `_reset_registry_for_tests()`; a script that calls `register_agent` twice with one name raises. Built-ins registered before a workspace's file agents win over them by name (the agent server does this at import).
- `register_builtins_agents()` also calls `register_default_tools(...)`. Without it, a built-in type that names `terminal`/`file_editor` raises `ValueError` at factory time for an unregistered tool. Example 41 does not call it, so only `animal_expert` is available there.
- Example 25 tells the model to use "the bash sub-agent"; the registered name is `bash-runner` (`bash` survives only as a deprecated alias).
- Sub-agent LLMs are copies of the parent's LLM with `stream=False`; `model:` in a definition switches to an LLM profile, not to a raw model string.
- There is no registered tool definition for `DelegateExecutor` in this checkout (only `TaskToolSet`/`TaskTool` call `register_tool`); `DelegateExecutor` is a library class exercised only by its unit tests.
- `TaskAction.max_turns` is deprecated and ignored.
- `tests/tools/delegate/test_delegation.py` emits `PydanticSerializationUnexpectedValue` warnings from `MagicMock` parents; they are harmless.
- All three examples run in `tests/examples/test_examples.py` (none is in `_EXCLUDED_EXAMPLES`) but only on the `test-examples` label.
- The user-level `.openhands/agents` directory is `$OH_PERSISTENCE_DIR/agents` (default `~/.openhands/agents`); only `.agents/agents` is resolved against `$HOME`.
- `TaskToolSet` builds its type list and examples when the agent initializes (the first `send_message()`), after workspace agents are registered; agents registered later still run but are not in the description.
- `TaskManager._resume_task` drops the parent's `prompt_cache_key` (see the known failure above); a resumed sub-agent starts a new cache shard.
- With a scripted `TestLLM` the `task:<id>` entries in `conversation_stats.usage_to_metrics` exist but are empty: the stats registry tracks only plain `LLM` objects.
