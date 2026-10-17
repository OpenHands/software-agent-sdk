# Planning, task tracking, oracle, ToM and workflow tools

Tools an SDK program adds to an agent to help it plan and coordinate rather than to touch the system directly. `task_tracker` keeps a structured task list (`plan` replaces it, `view` shows it, each item `todo`, `in_progress` or `done`) and saves it as `TASKS.json` in the conversation's persistence directory. The planning preset (`get_planning_agent(llm)`) is a read-only agent with `glob`, `grep` and `planning_file_editor`, a planning system prompt that fixes a five-section plan structure, and a larger summarizing condenser; `planning_file_editor` can view any file but edit only `PLAN.md`, which it creates with section headers at `.agents_tmp/PLAN.md` (or a legacy root `PLAN.md`, or an absolute `plan_path`). Example 24 chains a planning conversation and an execution conversation over the same workspace. `ask_oracle` sends the agent's question to a second model, the saved LLM profile named `oracle`, and returns its answer. `tom_consult` and `sleeptime_compute` wrap the `tom-swe` Theory-of-Mind agent: one gives personalized guidance for a vague request, the other indexes past conversations into a user model stored under the user persistence directory. `workflow` (from `workflow_tool_set`) runs a Python script the agent writes, `async def main(wf)`, in a restricted namespace where `wf.run_agent`, `wf.map_agents`, `wf.pipeline` and `wf.reduce_agent` fan work out to registered sub-agents with a concurrency cap and a one-hour limit.

Source: `openhands-tools/openhands/tools/task_tracker/`, `openhands-tools/openhands/tools/planning_file_editor/`, `openhands-tools/openhands/tools/preset/planning.py`, `openhands-tools/openhands/tools/ask_oracle/`, `openhands-tools/openhands/tools/tom_consult/`, `openhands-tools/openhands/tools/workflow/`, `openhands-sdk/openhands/sdk/context/prompts/sections/planning.py`

Needs: `llm` (examples, real oracle and ToM calls, workflow sub-agents)

Examples: `examples/01_standalone_sdk/24_planning_agent_workflow.py`, `examples/01_standalone_sdk/30_tom_agent.py`, `examples/01_standalone_sdk/52_dynamic_workflow.py`, `examples/01_standalone_sdk/58_ask_oracle_tool/main.py`

## Sub-features

- `S16.task-tracker-plan-view`: `TaskTrackerAction(command="plan", task_list=[TaskItem(title=..., notes=..., status=...)])` replaces the list and `command="view"` returns it as a numbered Markdown list with status icons. Unit: **none (untested)**. Example: `examples/01_standalone_sdk/01_hello_world.py` (tool attached). Real-LLM: none.
- `S16.task-tracker-persistence`: `TaskTrackerTool.create(conv_state)` points `save_dir` at the conversation's persistence directory (which includes the conversation id); each `plan` writes `TASKS.json` there and a new executor on the same directory loads it back. Unit: **none (untested)** for writing and reloading; `tests/cross/test_registry_directories.py` only checks `save_dir`. Example: none. Real-LLM: none.
- `S16.planning-tools`: `get_planning_tools(plan_path=None)` registers and returns `glob`, `grep` and `planning_file_editor`, forwarding `plan_path` as the editor's `params`; `glob` and `grep` register on import. Unit: `tests/tools/test_planning_preset.py`, `tests/cross/test_automatic_registration.py` (`test_grep_tool_automatic_registration`, `test_glob_tool_automatic_registration`, `test_planning_file_editor_tool_automatic_registration`). Example: `examples/01_standalone_sdk/24_planning_agent_workflow.py`. Real-LLM: `tests/integration/run_infer.py --tool-preset planning`.
- `S16.plan-file-location`: without `plan_path` the editor uses `<workspace>/.agents_tmp/PLAN.md`, or an existing legacy `<workspace>/PLAN.md` (with a warning); an absolute `plan_path` is used as given and its parent directory created; a relative `plan_path` raises `ValueError`; the tool description names the path. Unit: `tests/tools/planning_file_editor/test_planning_file_editor_tool.py`. Example: `examples/01_standalone_sdk/24_planning_agent_workflow.py`. Real-LLM: none.
- `S16.plan-file-headers`: a missing `PLAN.md` is created with the five numbered headers (`OBJECTIVE`, `CONTEXT SUMMARY`, `APPROACH OVERVIEW`, `IMPLEMENTATION STEPS`, `TESTING AND VALIDATION`) from `get_plan_headers()`. Unit: **none (untested)**; the tests check that the file exists, not its content. Example: `examples/01_standalone_sdk/24_planning_agent_workflow.py`. Real-LLM: none.
- `S16.plan-edit-restriction`: `planning_file_editor` allows `view` on any path but answers `create`, `str_replace`, `insert` and `undo_edit` on any file other than the plan with an error observation ("Operation ... is not allowed"). Unit: **none (untested)**; no test exercises `FileEditorExecutor(allowed_edits_files=...)`. Example: none. Real-LLM: none.
- `S16.planning-agent`: `get_planning_agent(llm)` builds an `Agent` with the planning tools, `system_prompt_filename="system_prompt_planning.j2"` (mapped to the planning prompt preset) with `plan_structure` filled in, and an `LLMSummarizingCondenser` (`max_size=100`, `keep_first=6`, `usage_id="planning_condenser"`). Unit: `tests/sdk/context/prompts/test_planning_registry.py` (the prompt section against a snapshot; `get_planning_agent` itself is not unit-tested). Example: `examples/01_standalone_sdk/24_planning_agent_workflow.py`. Real-LLM: none.
- `S16.plan-then-execute`: a planning conversation writes the plan and a second conversation with `get_default_agent(llm, cli_mode=True)` in the same workspace reads `.agents_tmp/PLAN.md` and implements it. Unit: **none (untested)**. Example: `examples/01_standalone_sdk/24_planning_agent_workflow.py`. Real-LLM: none.
- `S16.oracle-tool`: `Tool(name="ask_oracle")` resolves without params (params are rejected), and its description tells the agent when to ask for a second opinion. Unit: `tests/tools/ask_oracle/test_ask_oracle.py` (`test_ask_oracle_tool_added_by_name`, `test_ask_oracle_tool_rejects_parameters`, `test_ask_oracle_tool_description_guides_second_opinion_usage`). Example: `examples/01_standalone_sdk/58_ask_oracle_tool/main.py`. Real-LLM: none.
- `S16.oracle-consult`: a call loads the `oracle` profile from the conversation's profile store (`LocalConversation(profile_store_dir=...)`), sends the question with context, returns the answer as the observation, and books cost under an `oracle` usage id. Unit: `tests/tools/ask_oracle/test_ask_oracle.py::test_ask_oracle_tool_returns_oracle_recommendation`. Example: `examples/01_standalone_sdk/58_ask_oracle_tool/main.py`. Real-LLM: none.
- `S16.oracle-errors`: without an active conversation, without an `oracle` profile ("Save one to enable it"), or with an empty answer, the tool returns an error observation instead of raising. Unit: `tests/tools/ask_oracle/test_ask_oracle.py` (`test_ask_oracle_tool_requires_active_conversation`, `test_ask_oracle_tool_reports_missing_profile`, `test_ask_oracle_tool_reports_empty_oracle_response`). Example: none. Real-LLM: none.
- `S16.tom-consult`: `Tool(name="tom_consult", params={"enable_rag": True, "llm_model": ..., "api_key": ..., "api_base": ...})` lets the agent ask the ToM agent about the user's last message or a custom query and get `suggestions`, `confidence` and `reasoning`; it declares no resource keys. Unit: `tests/tools/tom_consult/test_tom_consult_tool.py` (declared resources only; the consult call is not tested). Example: `examples/01_standalone_sdk/30_tom_agent.py`. Real-LLM: none.
- `S16.tom-sleeptime`: `sleeptime_compute` (often called up front with `conversation.execute_tool("sleeptime_compute", SleeptimeComputeAction())`) indexes stored conversations and reports `sessions_processed`. Unit: **none (untested)**. Example: `examples/01_standalone_sdk/30_tom_agent.py`. Real-LLM: none.
- `S16.tom-storage-root`: both ToM tools keep their user model in a `LocalFileStore` rooted at the user persistence directory (`~/.openhands`, or `OH_PERSISTENCE_DIR`). Unit: `tests/cross/test_persistence_dir_call_sites.py::test_tom_consult_file_store_root`. Example: `examples/01_standalone_sdk/30_tom_agent.py`. Real-LLM: none.
- `S16.workflow-toolset`: `Tool(name="workflow_tool_set", params={"sub_agent_scope": {...}})` yields one `workflow` tool (`name`, `script`, `max_concurrency` 1 to 64, default 8); the tool needs a local conversation and returns an error observation without one. Unit: `tests/tools/workflow/test_workflow_tool.py` (`test_workflow_executor_success_path`, `test_workflow_executor_returns_error_observation_without_conversation`), `tests/cross/test_default_tool_names.py::test_delegation_names_are_the_tools_that_take_a_sub_agent_scope`. Example: `examples/01_standalone_sdk/52_dynamic_workflow.py`. Real-LLM: none.
- `S16.workflow-script-validation`: a script must define `async def main(wf)`; imports, unsafe builtins and modules, private `wf` attributes and `wf.close()` are rejected before it runs. Unit: `tests/tools/workflow/test_workflow_tool.py` (`test_validate_workflow_script_rejects_*`). Example: none. Real-LLM: none.
- `S16.workflow-fan-out`: `wf.run_agent(prompt, subagent_type="general-purpose")` starts one sub-agent task and returns its result; `wf.map_agents(items, prompt, ...)` runs one per item under the concurrency cap and reports every failure; `wf.reduce_agent` summarizes; `wf.flatten` flattens one level. Unit: `tests/tools/workflow/test_workflow_tool.py` (`test_execute_workflow_script_runs_map_and_reduce`, `test_run_agent_returns_task_result`, `test_map_agents_uses_context_default_concurrency_when_none_given`, `test_map_agents_reports_all_sub_agent_failures`, `test_map_agents_respects_context_concurrency_cap`, `test_workflow_context_helper_flattens_one_level`). Example: `examples/01_standalone_sdk/52_dynamic_workflow.py`. Real-LLM: none.
- `S16.workflow-pipeline`: `wf.pipeline(items, stage1, stage2, ...)` runs sync or async stages per item with no barrier between stages, keeps item order, and drops a failed item to `None`; it needs at least one stage. Unit: `tests/tools/workflow/test_workflow_tool.py` (`test_pipeline_*`). Example: none. Real-LLM: none.
- `S16.workflow-errors-and-output`: the script can catch common exceptions and `ExceptionGroup`s; uncaught errors come back formatted (with group details); the input `wf.reduce_agent` sends its sub-agent is cut to 12,000 characters, by whole trailing elements ("items omitted to fit") or characters; the script's own return value reaches the LLM whole. Unit: `tests/tools/workflow/test_workflow_tool.py` (`test_workflow_script_can_catch_*`, `test_format_exception_includes_exception_group_details`, `test_format_value_*`). Example: none. Real-LLM: none.
- `S16.workflow-lifecycle`: closing the context closes the task manager once, and `run_agent` after close raises. Unit: `tests/tools/workflow/test_workflow_tool.py` (`test_workflow_context_close_propagates_to_manager`, `test_workflow_context_close_is_idempotent`, `test_run_agent_raises_after_close`). Example: none. Real-LLM: none.

## How to get to it (agent POV)

- Task tracker: `Tool(name="task_tracker")` (in every preset); direct use `TaskTrackerExecutor(save_dir=...)` with `TaskTrackerAction` and `TaskItem` from `openhands.tools.task_tracker.definition`. `S16.task-tracker-*`.
- Planning: `from openhands.tools.preset.planning import get_planning_agent, get_planning_tools, get_plan_headers, format_plan_structure`; `Tool(name="planning_file_editor", params={"plan_path": "/abs/PLAN.md"})`; direct use `PlanningFileEditorExecutor(workspace_root=..., plan_path=...)` from `openhands.tools.planning_file_editor.impl`. `S16.planning-*`, `S16.plan-*`.
- Oracle: save a profile first, `LLMProfileStore(dir).save("oracle", LLM(...), include_secrets=True)` (`ORACLE_PROFILE_NAME` from `openhands.tools.ask_oracle`), then `Agent(tools=[Tool(name=AskOracleTool.name)])` and `LocalConversation(..., profile_store_dir=dir)`. `S16.oracle-*`.
- ToM: `from openhands.tools.tom_consult import TomConsultTool, SleeptimeComputeTool, SleeptimeComputeAction`; `Tool(name=TomConsultTool.name, params=tom_params)`; `OH_PERSISTENCE_DIR` moves the user-model store. `S16.tom-*`.
- Workflow: `from openhands.tools.workflow import WorkflowToolSet`; `Tool(name=WorkflowToolSet.name)`; sub-agents come from `register_agent_if_absent(name=..., factory_func=..., description=...)` (`openhands.sdk.subagent`) or the built-ins; `WorkflowTool.create(executor=...)` injects a custom executor. `S16.workflow-*`.
- Integration runner: `tests/integration/run_infer.py --tool-preset planning` swaps in `get_planning_tools()`.

## Driving it

Preconditions:
- `uv sync --dev` has run (it installs `tom-swe`, a required dependency of `openhands-tools`); `control-sdk` is on `PATH` (`export PATH="$PWD/.agents/skills/verify-sdk/scripts:$PATH"`). `sdk-map run` does both.
- The oracle and ToM programs point their second model at a local OpenAI-compatible endpoint (`programs/S16/fake_openai.py`), so they need no key and no network.
- Live bullets: `DEEPSEEK_API_KEY` exported.

- **A task list across two turns (`S16.task-tracker-plan-view`, `S16.task-tracker-persistence`).** A scripted agent with only `task_tracker` views the empty list, plans three tasks and finishes. A second `send` (a new process, so a new executor) views the list it reloads from `TASKS.json`, marks the parser done, and sends a task with an invalid status.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  CID=$(control-sdk conversation new --tools task_tracker --script S16/task_tracker.json --field id)
  control-sdk conversation send "$CID" --text "Plan the parser work." --run \
    --check execution_status eq finished --check last_agent_message eq "Planned three tasks."
  control-sdk conversation send "$CID" --text "The parser is done, update the list." --run \
    --check execution_status eq finished --check last_agent_message eq "Parser done, tests in progress."
  control-sdk conversation events "$CID" --kind ObservationEvent \
    --check events.0.text contains "No task list found" \
    --check events.1.text contains "updated with 3 item" \
    --check events.3.text contains "# Task List" --check events.3.text contains "Read the spec" \
    --check events.3.text contains "docs/spec.md" --check events.3.text contains "Add tests"
  control-sdk conversation events "$CID" --kind AgentErrorEvent \
    --check count eq 1 --check events.0.error contains "Input should be 'todo', 'in_progress' or 'done'"
  HEX=$(echo "$CID" | tr -d -)
  control-sdk exec --expect-output '"status": "in_progress"' -- cat "$SDK_VERIFY_RUN/conversations/$HEX/TASKS.json"
  control-sdk exec --expect-output 2 -- grep -c '"status": "done"' "$SDK_VERIFY_RUN/conversations/$HEX/TASKS.json"
  ```
  The rejected plan leaves `TASKS.json` as the second plan wrote it: two tasks done, one in progress.
- **Plan, then execute (`S16.planning-tools`, `S16.plan-file-location`, `S16.plan-file-headers`, `S16.plan-edit-restriction`, `S16.planning-agent`, `S16.plan-then-execute`).** The flow of example 24 with scripted models. `get_planning_agent(llm)` greps and globs a small project, views the new `.agents_tmp/PLAN.md`, is refused a `create` and a `str_replace` outside the plan, and fills in the objective. A second conversation, `get_default_agent(llm, cli_mode=True)` in the same workspace, reads the plan, edits `app/main.py` and runs it.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S16/planning --arg default \
    --check planner_specs eq '["glob", "grep", "planning_file_editor"]' \
    --check planner_tools eq '["finish", "glob", "grep", "planning_file_editor", "think"]' \
    --check planner_results.0.text contains "app/main.py" --check planner_results.1.text contains "app/main.py" \
    --check plan_headers eq '["# 1. OBJECTIVE", "# 2. CONTEXT SUMMARY", "# 3. APPROACH OVERVIEW", "# 4. IMPLEMENTATION STEPS", "# 5. TESTING AND VALIDATION"]' \
    --check headers_match_preset eq true \
    --check planner_results.3.is_error eq true --check planner_results.3.text contains "Operation 'create' is not allowed" \
    --check planner_results.4.is_error eq true --check planner_results.4.text contains "Operation 'str_replace' is not allowed" \
    --check planner_results.5.is_error eq false \
    --check main_untouched eq true --check new_file_absent eq true --check plan_has_objective_text eq true \
    --check description_names_plan eq true --check prompt_has_plan_structure eq true \
    --check condenser eq LLMSummarizingCondenser --check condenser_max_size eq 100 \
    --check condenser_keep_first eq 6 --check condenser_usage_id eq planning_condenser \
    --check executor_saw_plan eq true --check executor_output contains hello \
    --check main_after contains "return 'hello'"
  ```
- **Where the plan lives (`S16.plan-file-location`, `S16.planning-tools`).** `Tool(name="planning_file_editor")` in a workspace with a legacy root `PLAN.md`, with an absolute `plan_path`, and with a relative one.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S16/planning --arg legacy \
    --check plan_location matches "/workspace/PLAN.md$" --check legacy_kept eq true --check default_plan_created eq false
  control-sdk program S16/planning --arg absolute \
    --check spec_params.plan_path matches "/plans/deep/PLAN.md$" \
    --check plan_location matches "/plans/deep/PLAN.md$" --check absolute_created eq true --check default_plan_created eq false
  control-sdk program S16/planning --arg relative \
    --check spec_params.plan_path eq plans/PLAN.md --check error contains "plan_path must be an absolute path"
  ```
  `get_planning_tools(plan_path=...)` forwards the path as the editor's `params` (`spec_params`). The relative path is refused when the conversation resolves the tool, not when the spec is built.
- **Ask the Oracle (`S16.oracle-tool`, `S16.oracle-consult`, `S16.oracle-errors`).** As example 58 wires it: an `oracle` profile saved in the conversation's profile store, `LocalConversation(profile_store_dir=...)`, and a scripted agent that asks one question with context. The profile's model is the local endpoint. Each mode also calls the executor with no conversation.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S16/oracle --arg answer \
    --check status eq finished --check is_error eq false --check text eq "Use one typed setting." \
    --check agent_model eq test-model --check usage_ids eq '["oracle:oracle"]' \
    --check oracle_requests eq 1 --check oracle_saw_question eq true --check oracle_saw_context eq true \
    --check oracle_got_tools eq false --check description_mentions_second_opinion eq true
  control-sdk program S16/oracle --arg empty \
    --check is_error eq true --check text contains "The Oracle did not return a response."
  control-sdk program S16/oracle --arg missing \
    --check is_error eq true --check text contains "no profile named 'oracle' was found. Save one to enable it." \
    --check oracle_requests eq 0 \
    --check no_conversation_is_error eq true --check no_conversation_text contains "without an active conversation"
  control-sdk program S16/oracle --arg params \
    --check error contains "AskOracleTool does not accept parameters"
  ```
- **Theory of Mind (`S16.tom-consult`, `S16.tom-sleeptime`, `S16.tom-storage-root`).** As example 30 wires them, with `tom_params` pointing at the local endpoint. Sleeptime compute runs once on an empty store and once after earlier conversations are saved under the user persistence directory; then a scripted agent consults Tom.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S16/tom \
    --check store_root_is_env eq true \
    --check before_processed eq 0 --check before_message contains "No valid conversations to index" \
    --check sessions_on_disk ge 1 --check files_under_root contains "usermodeling/cleaned_sessions/" \
    --check tom_requests ge 1 \
    --check consult_tool eq tom_consult --check consult_is_error eq false --check consult_text contains "Tom agent's guidance"
  ```
  The store is under `OH_PERSISTENCE_DIR` (the run sets it), not `~/.openhands`. Sleeptime compute cleans the stored sessions into `usermodeling/cleaned_sessions/` before it calls the model. The local endpoint cannot produce `tom-swe`'s structured analysis, so the indexing call ends in a parse error (`after_error`), and the consult returns Tom's "[CRITICAL] Tom agent cannot provide consultation" fallback as a normal observation. Real suggestions need the live bullet.
- **Workflows over registered sub-agents (`S16.workflow-*`).** A program registers three sub-agent types with `register_agent_if_absent`: `summarizer` finishes with "report: all good", `breaker` fails on its first model call, and `sleeper` takes a second to start. A scripted agent with `workflow_tool_set` then runs twelve workflows: fan-out, pipeline, reduce and flatten; caught and uncaught sub-agent failures; five scripts the sandbox must refuse; a 100,000-element result; a reduce over 60 long findings; and four sleepers at `max_concurrency` 1 and 4. Last, the program uses `WorkflowContext` after close and `WorkflowExecutor` with no conversation.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S16/workflow --timeout 300 \
    --check status eq finished --check tools_map eq '["finish", "think", "workflow"]' \
    --check steps.fan_out.status eq completed \
    --check steps.fan_out.text contains "'one': 'report: all good'" \
    --check steps.fan_out.text contains "'many': ['report: all good', 'report: all good', 'report: all good']" \
    --check steps.fan_out.text contains "'flat': [1, 2, 3, 4]" \
    --check steps.fan_out.text contains "'piped': [-10, None, 10]" \
    --check steps.fan_out.text contains "'reduced': 'report: all good'" \
    --check steps.caught.is_error eq false --check steps.caught.text contains "caught 2" --check steps.caught.text contains "single: Conversation run failed" \
    --check steps.uncaught.is_error eq true --check steps.uncaught.text contains "map_agents: one or more sub-agents failed" \
    --check steps.uncaught.text contains "[1] [item 1]" --check steps.uncaught.text contains "[2] [item 2]" \
    --check steps.import.text eq "Workflow scripts may not import modules" \
    --check steps.private.text contains "may not access private wf attributes" \
    --check steps.close.text contains "call wf.close" \
    --check steps.open_call.text contains "may not call" \
    --check steps.no_main.text contains "Workflow script must define exactly one async main" \
    --check steps.huge.is_error eq false --check steps.huge.text_len eq 688890 \
    --check steps.reduce_huge.text eq "report: all good" \
    --check reducer_prompt_seen eq true --check reducer_prompt_truncated eq true \
    --check steps.serial.seconds ge 4 --check steps.parallel.seconds lt 2.5 \
    --check run_after_close contains "WorkflowContext is already closed" \
    --check no_conversation_error eq true --check no_conversation_text contains "requires a local conversation"
  ```
  The pipeline divides `100` by `x - 20`, so the middle item raises `ZeroDivisionError` and becomes `None` while the others keep their order.
- **Planning workflow with a real model (`S16.planning-agent`, `S16.plan-then-execute`).** Live; the example plans and builds in a temp workspace of its own.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output "WORKFLOW COMPLETE" -- python examples/01_standalone_sdk/24_planning_agent_workflow.py
  ```
  It prints the plan path, the execution result and the files created.
- **Planning preset through the integration runner (`S16.planning-tools`).** Live.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env -- python tests/integration/run_infer.py --llm-config '{"model": "deepseek/deepseek-flash"}' --tool-preset planning --eval-ids t01_fix_simple_typo
  ```
  The planning agent can edit only its plan, so t01's file check is not a fair verdict here; read the run for the preset's tools (`glob`, `grep`, `planning_file_editor`) and the plan it writes.
- **The Oracle with real models (`S16.oracle-consult`).** Live. Example 58 falls back to the All-Hands proxy when `LLM_BASE_URL` is unset, so point it at DeepSeek.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --env LLM_BASE_URL=https://api.deepseek.com --expect-output "Total cost" -- python examples/01_standalone_sdk/58_ask_oracle_tool/main.py
  ```
- **Theory of Mind with a real model (`S16.tom-consult`, `S16.tom-sleeptime`).** Live. The run's `HOME` holds the example's `~/.openhands`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output "Tom agent consultation example completed" -- python examples/01_standalone_sdk/30_tom_agent.py
  ```
- **A workflow written by a real model (`S16.workflow-toolset`, `S16.workflow-fan-out`).** Live.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output EXAMPLE_COST -- python examples/01_standalone_sdk/52_dynamic_workflow.py
  ```
  The parent agent writes and runs a workflow that fans `coverage_auditor` sub-agents over the repository and prints the reducer's report.

## Gotchas

- `TaskTrackerExecutor`'s docstring says tasks go to `TASKS.md`; the file actually written and read is `TASKS.json`.
- `TaskTrackerTool` only persists when the conversation has a persistence directory; with none, `save_dir` is `None` and the list lives in memory.
- Example 30 stores its conversations and the ToM user model under `~/.openhands` (it sets `persistence_dir` to `~/.openhands/conversations`); set `OH_PERSISTENCE_DIR` to keep a test run out of your home directory. Its ToM tools get the API key in plain `params`.
- Example 58 defaults `LLM_BASE_URL` to `https://llm-proxy.app.all-hands.dev` and uses `ASK_ORACLE_PRIMARY_MODEL` / `ASK_ORACLE_MODEL` to pick the two models; without them both are `LLM_MODEL` (default `openai/gpt-5.5`).
- Example 52 sets `max_iteration_per_run=6`; a model that needs more turns to write the script stops early. Its sub-agents run against the current repository (read-only by instruction only).
- The workflow tool runs the script with `asyncio.run` in the tool thread and a hard one-hour limit (`_WORKFLOW_TIMEOUT_SECONDS`); the sandbox is an AST check plus restricted globals, not process isolation.
- `get_planning_agent` passes `system_prompt_filename="system_prompt_planning.j2"`, a file that no longer exists; the name is mapped to the planning prompt preset in `openhands-sdk/openhands/sdk/agent/base.py`, so do not look for the template.
- The workflow's return value is not truncated: a script that returns `list(range(100000))` hands the model a 688,890-character observation. Only the reducer's input is capped (`_MAX_REDUCE_INPUT_CHARS`), and scripts themselves are capped at 20,000 characters.
- `tom_consult` returns its fallback ("[CRITICAL] Tom agent cannot provide consultation ...") with `is_error` false when the ToM model's answer cannot be parsed; check the text, not the error flag.
- A `task_tracker` call with an invalid status (anything but `todo`, `in_progress`, `done`) never reaches the executor: it is an `AgentErrorEvent` from validation, and the saved list is unchanged.
- `planning_file_editor` refuses a relative `plan_path` only when a conversation resolves the tool; `get_planning_tools(plan_path="plans/PLAN.md")` builds the spec without complaint.
