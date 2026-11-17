# Planning, task tracking, oracle, ToM, workflow, reaction, message-edit, mention and ask-user tools

Tools an SDK program adds to an agent to help it plan and coordinate rather than to touch the system directly. `task_tracker` keeps a structured task list (`plan` replaces it, `view` shows it, each item `todo`, `in_progress` or `done`) and saves it as `TASKS.json` in the conversation's persistence directory. The planning preset (`get_planning_agent(llm)`) is a read-only agent with `glob`, `grep` and `planning_file_editor`, a planning system prompt that fixes a five-section plan structure, and a larger summarizing condenser; `planning_file_editor` can view any file but edit only `PLAN.md`, which it creates with section headers at `.agents_tmp/PLAN.md` (or a legacy root `PLAN.md`, or an absolute `plan_path`). Example 24 chains a planning conversation and an execution conversation over the same workspace. `ask_oracle` sends the agent's question to a second model, the saved LLM profile named `oracle`, and returns its answer. `tom_consult` and `sleeptime_compute` wrap the `tom-swe` Theory-of-Mind agent: one gives personalized guidance for a vague request, the other indexes past conversations into a user model stored under the user persistence directory. `workflow` (from `workflow_tool_set`) runs a Python script the agent writes, `async def main(wf)`, in a restricted namespace where `wf.run_agent`, `wf.map_agents`, `wf.pipeline` and `wf.reduce_agent` fan work out to registered sub-agents with a concurrency cap and a one-hour limit. `react` and `unreact` (`openhands.tools.reaction`) let the agent put a small emoji (`eyes`, `+1`, `white_check_mark`, `tada`, `thinking_face`, `heart`) on a message, by default the user's latest one, as a sign it saw it; each successful call records a non-LLM `ReactionEvent` right after the tool's observation, and the program's user reacts with `conversation.react(emoji, target_event_id=None, removed=False)`, which by default targets the agent's latest message or `finish` and does not start a run. `edit_message` (`openhands.tools.message_edit`) replaces text in one of the agent's earlier messages or `finish` answers, like `str_replace` in a file: each successful call records a non-LLM `MessageEditEvent` carrying the message's whole new text right after the observation, `message_text(...)` reads a message as it stands after its latest edit, and the LLM keeps seeing the message as first written. `find_conversations` and `read_conversation` (`openhands.tools.conversation_mentions`) read another conversation through an agent server's REST API (`OH_INTERNAL_SERVER_URL`, default `http://127.0.0.1:8000`), so in a program without a server they only return error observations; what they return with a server is the agent-server map's F38. The link format they write, `[Title](conversation:<id hex>)`, comes from `conversation_mention(title, id)` in `openhands.sdk.conversation.types`, which works anywhere. `ask_user` (`openhands.tools.ask_user`) asks the user one question with two to four options (each a `label` and a `consequence`) and an optional `recommended` index; the call ends the agent's turn like `finish` does, and the question stays pending (`find_pending_ask(conversation.state.active_branch())` in `openhands.sdk.event.ask_user`) until a message of the user's own follows it. The program's user answers by writing in the chat, or picks an option with `conversation.answer_ask(ask_id, option_index, text)`, which records a non-LLM `AskUserAnswerEvent` and returns the user message the agent reads (`[Answer to your question: "..."]` then `Option N: label`); sending that message closes the question. While a question is pending, nothing else continues the turn over it: Stop hooks, iterative refinement and `run_goal` (S04) all wait for the answer.

Source: `openhands-tools/openhands/tools/task_tracker/`, `openhands-tools/openhands/tools/planning_file_editor/`, `openhands-tools/openhands/tools/preset/planning.py`, `openhands-tools/openhands/tools/ask_oracle/`, `openhands-tools/openhands/tools/tom_consult/`, `openhands-tools/openhands/tools/workflow/`, `openhands-sdk/openhands/sdk/context/prompts/sections/planning.py`, `openhands-tools/openhands/tools/reaction/`, `openhands-sdk/openhands/sdk/event/reaction.py`, `openhands-tools/openhands/tools/message_edit/`, `openhands-sdk/openhands/sdk/event/message_edit.py`, `openhands-tools/openhands/tools/conversation_mentions/`, `openhands-tools/openhands/tools/ask_user/`, `openhands-sdk/openhands/sdk/event/ask_user.py`

Needs: `llm` (examples, real oracle and ToM calls, workflow sub-agents)

Examples: `examples/01_standalone_sdk/24_planning_agent_workflow.py`, `examples/01_standalone_sdk/30_tom_agent.py`, `examples/01_standalone_sdk/52_dynamic_workflow.py`, `examples/01_standalone_sdk/58_ask_oracle_tool/main.py`

## Sub-features

- `S16.task-tracker-plan-view`: `TaskTrackerAction(command="plan", task_list=[TaskItem(title=..., notes=..., status=...)])` replaces the list and `command="view"` returns it as a numbered Markdown list with status icons. Unit: **none (untested)**. Example: `examples/01_standalone_sdk/01_hello_world.py` (tool attached). Real-LLM: none.
- `S16.task-tracker-persistence`: `TaskTrackerTool.create(conv_state)` points `save_dir` at the conversation's persistence directory (which includes the conversation id); each `plan` writes `TASKS.json` there and a new executor on the same directory loads it back. Unit: **none (untested)** for writing and reloading; `tests/cross/test_registry_directories.py` only checks `save_dir`. Example: none. Real-LLM: none.
- `S16.planning-tools`: `get_planning_tools(plan_path=None)` registers and returns `glob`, `grep` and `planning_file_editor`, forwarding `plan_path` as the editor's `params`; `glob` and `grep` register on import. Unit: `tests/tools/test_planning_preset.py`, `tests/cross/test_automatic_registration.py` (`test_grep_tool_automatic_registration`, `test_glob_tool_automatic_registration`, `test_planning_file_editor_tool_automatic_registration`). Example: `examples/01_standalone_sdk/24_planning_agent_workflow.py`. Real-LLM: none that can pass (see Gotchas: the runner's planning preset fails its file-editing tasks by construction).
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
- `S16.reactions`: `Tool(name=ReactTool.name)` / `Tool(name=UnreactTool.name)` (`react`, `unreact`) put an emoji on, or take it off, the user's latest message or the `message_id` given; each call returns `Reacted :<emoji>:.` / `Removed :<emoji>:.` and the agent records one agent `ReactionEvent` right after the observation; an emoji outside the six is rejected as a tool validation error. `conversation.react(emoji)` records a user `ReactionEvent` on the agent's latest answer (`finish` or message) with its text ending, raises `ValueError` for an unknown emoji, leaves the run status alone, and the agent sees it as one `[The user reacted ...]` line on its next turn, while its own reactions stay out of what it sees. Unit: `tests/tools/reaction/test_reaction_tools.py`. Example: none. Real-LLM: none.
- `S16.message-edit-tool`: `Tool(name=EditMessageTool.name)` (`edit_message`, no params) takes `old_str` and `new_str`; on success the observation reads `Edited. The message now reads:` plus the whole new text. `old_str` must occur exactly once across the agent's messages and `finish` answers as they read after earlier edits; text that is only in a user message, missing, empty or unchanged is an error observation and records nothing. Two edits of the same message in one response: the first stands and the later one becomes an error observation (`Not edited: an earlier call in this response already edits this message`) quoting the message as the first edit left it. Unit: `tests/tools/message_edit/test_message_edit.py`. Example: none. Real-LLM: none.
- `S16.message-edit-event`: each successful `edit_message` call records one agent `MessageEditEvent(target_event_id, text)` right after its `ObservationEvent`; `message_text(state.walk_back(until=message.id), message)` (`openhands.sdk.event.message_edit`) returns the latest edit's text, a user `conversation.react(...)` on an edited message quotes the edited text, and the events round-trip through JSON and survive reloading the persisted conversation. Unit: `tests/tools/message_edit/test_message_edit.py` (`test_agent_records_the_edit_after_the_observation`, `test_edit_round_trips_as_an_event`, `test_a_users_reaction_quotes_the_edited_text`). Example: none. Real-LLM: none.
- `S16.message-edit-llm-view`: `MessageEditEvent` is not an `LLMConvertibleEvent` and is not in `conversation.state.view`; the LLM's next call, like `LLMConvertibleEvent.events_to_messages(...)`, still holds the assistant message as first written, and sees the edits only as its own `edit_message` calls and their results. Unit: `tests/tools/message_edit/test_message_edit.py::test_the_agent_sees_its_call_but_not_the_edit_event`. Example: none. Real-LLM: none.
- `S16.conversation-mention-link`: `conversation_mention(title, conversation_id)` returns `[Title](conversation:<32-hex id>)` for a UUID or a dashed id string; the title is flattened to one line without brackets, parentheses, quotes, backticks or backslashes, cut to 80 characters, and a blank title reads `untitled conversation`. Unit: `tests/sdk/conversation/test_conversation_mention.py`. Example: none. Real-LLM: none.
- `S16.conversation-mention-tools`: `Tool(name=FindConversationsTool.name)` and `Tool(name=ReadConversationTool.name)` (`find_conversations`, `read_conversation`) resolve without params (params raise `ValueError`), are read-only, and add the mention-link guidance to the system prompt. In a local conversation they call the agent server at `OH_INTERNAL_SERVER_URL`: with no server there, each call is an error observation (`Could not list conversations: ...`, `Could not read conversation <hex>: ...`), and `read_conversation` with text that holds no mention or id is an error before any request. Unit: `tests/tools/conversation_mentions/test_conversation_mention_tools.py`. Example: none. Real-LLM: none.
- `S16.ask-user-tool`: `Tool(name=AskUserTool.name)` (`ask_user`, no params) records the question as an `ActionEvent` holding an `AskUserAction(question, options=[AskUserOption(label, consequence)], recommended)`; its observation reads `The question is shown to the user. Your turn ends here; ...`, the run ends `finished` after that one LLM call with no `finish`, and `find_pending_ask` returns the call's id, labels and recommendation, also after the persisted conversation is reloaded. One option or a `recommended` index past the options is an `AgentErrorEvent` (tool validation); a blank label is an error observation (`Each option needs a nonblank label. ...`); neither leaves a question pending nor ends the turn. A second `ask_user` in the same response gets an error observation (`Not shown: this response already ends the turn ...`) and only the first is pending; with `AlwaysConfirm` the question needs no confirmation. Unit: `tests/tools/ask_user/test_ask_user.py` (`test_options_are_checked`, `test_asking_ends_the_turn_and_leaves_the_question_pending`, `test_an_invalid_question_does_not_end_the_turn`, `test_only_the_first_question_in_a_response_is_asked`, `test_asking_needs_no_confirmation_even_when_every_action_does`, `test_the_question_survives_a_restart`). Example: none. Real-LLM: none.
- `S16.ask-user-answer`: `conversation.answer_ask(ask_id, option_index=None, text=None)` raises `ValueError` for an id that is not the pending question (`That question is not waiting for an answer.`), an option that does not exist (`The question has 2 options; there is no option 2.`) and an answer with neither an option nor words, recording nothing; otherwise it records a user `AskUserAnswerEvent(ask_id, option_index, option_label, text)` (words trimmed, parent the call's observation) and returns the message `[Answer to your question: "<question>"]`, `Option <n>: <label>`, then the words, which keeps two equal labels apart. The question stays pending until the program sends that message; the next `run()` answers, and a second tap is refused. Unit: `tests/tools/ask_user/test_ask_user.py` (`test_a_tap_records_the_answer_and_the_message_the_agent_reads`, `test_equal_labels_preserve_which_option_the_user_selected`, `test_an_answer_is_refused_for_another_question_or_option`, `test_a_second_tap_is_refused`, `test_a_tap_whose_message_was_lost_can_be_sent_again`, `test_the_answer_event_round_trips`). Example: none. Real-LLM: none.
- `S16.ask-user-chat-reply`: an ordinary user message answers the question in words and closes it; a message relayed with a `sender` (from another conversation) does not. Unit: `tests/tools/ask_user/test_ask_user.py` (`test_a_chat_reply_answers_the_question`, `test_a_chat_reply_then_runs_cleanly`, `test_a_message_from_another_conversation_does_not_answer`, `test_the_user_s_own_message_is_what_closes_it`). Example: none. Real-LLM: none.
- `S16.ask-user-holds-completion`: a pending question is not consumed by completion logic: a Stop hook that denies completion does not run, and the agent gets no extra turn, after the agent asks (with `run()` and `arun()`), while after the user's answer the same hook runs on the next completion; `ask_user` and `finish` in one response with iterative refinement on end the turn on the question without a refinement message. Unit: `tests/tools/ask_user/test_ask_user.py` (`test_stop_hooks_wait_for_an_answer_but_still_check_completion`, `test_a_question_takes_precedence_over_finish_refinement`). Example: none. Real-LLM: none.

## How to get to it (agent POV)

- Task tracker: `Tool(name="task_tracker")` (in every preset); direct use `TaskTrackerExecutor(save_dir=...)` with `TaskTrackerAction` and `TaskItem` from `openhands.tools.task_tracker.definition`. `S16.task-tracker-*`.
- Planning: `from openhands.tools.preset.planning import get_planning_agent, get_planning_tools, get_plan_headers, format_plan_structure`; `Tool(name="planning_file_editor", params={"plan_path": "/abs/PLAN.md"})`; direct use `PlanningFileEditorExecutor(workspace_root=..., plan_path=...)` from `openhands.tools.planning_file_editor.impl`. `S16.planning-*`, `S16.plan-*`.
- Oracle: save a profile first, `LLMProfileStore(dir).save("oracle", LLM(...), include_secrets=True)` (`ORACLE_PROFILE_NAME` from `openhands.tools.ask_oracle`), then `Agent(tools=[Tool(name=AskOracleTool.name)])` and `LocalConversation(..., profile_store_dir=dir)`. `S16.oracle-*`.
- ToM: `from openhands.tools.tom_consult import TomConsultTool, SleeptimeComputeTool, SleeptimeComputeAction`; `Tool(name=TomConsultTool.name, params=tom_params)`; `OH_PERSISTENCE_DIR` moves the user-model store. `S16.tom-*`.
- Workflow: `from openhands.tools.workflow import WorkflowToolSet`; `Tool(name=WorkflowToolSet.name)`; sub-agents come from `register_agent_if_absent(name=..., factory_func=..., description=...)` (`openhands.sdk.subagent`) or the built-ins; `WorkflowTool.create(executor=...)` injects a custom executor. `S16.workflow-*`.
- Reactions: `from openhands.tools.reaction import ReactTool, UnreactTool`; `Agent(tools=[Tool(name=ReactTool.name), Tool(name=UnreactTool.name)])` (not in any preset); `conversation.react("heart")`, `ReactionEvent` from `openhands.sdk.event`. `S16.reactions`.
- Message edits: `from openhands.tools.message_edit import EditMessageTool`; `Agent(tools=[Tool(name=EditMessageTool.name)])` (not in any preset); `MessageEditEvent` from `openhands.sdk.event`, `message_text` from `openhands.sdk.event.message_edit`. `S16.message-edit-*`.
- Conversation mentions: `from openhands.sdk.conversation.types import conversation_mention`; `from openhands.tools.conversation_mentions import FindConversationsTool, ReadConversationTool` (not in any preset; the agent server adds them for its `conversation_mentions` experimental feature, F38). `OH_INTERNAL_SERVER_URL` and `OH_SESSION_API_KEYS_0`/`SESSION_API_KEY` pick the server and its key. `S16.conversation-mention-*`.
- Ask user: `from openhands.tools.ask_user import AskUserTool`; `Agent(tools=[Tool(name=AskUserTool.name)])` (not in any preset, and not user-selectable in agent settings; the agent server adds it for its `ask_user` experimental feature, F40); `conversation.answer_ask(ask_id, option_index, text)`; `AskUserAnswerEvent`, `find_pending_ask`, `answer_message_text` and `ANSWER_HEADER_PREFIX` from `openhands.sdk.event.ask_user`. `S16.ask-user-*`; `run_goal` and questions: S04 `S04.goal-asked`.
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
    --check planner_tools eq '["finish", "glob", "grep", "planning_file_editor"]' \
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
    --check status eq finished --check is_error eq true \
    --check text contains "The Oracle encountered an error and did not return a response: LLMNoResponseError" \
    --check oracle_requests eq 1
  control-sdk program S16/oracle --arg missing \
    --check is_error eq true --check text contains "no profile named 'oracle' was found. Save one to enable it." \
    --check oracle_requests eq 0 \
    --check no_conversation_is_error eq true --check no_conversation_text contains "without an active conversation"
  control-sdk program S16/oracle --arg params \
    --check error contains "AskOracleTool does not accept parameters"
  ```
  An empty answer never reaches the tool: the oracle's `LLM` raises `LLMNoResponseError` for a completion with no content, tool calls or reasoning (retried up to the profile's `num_retries`, 0 here), and the tool reports that error as its observation. The tool's own "The Oracle did not return a response." is left for a reply whose only content is reasoning.
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
    --check status eq finished --check tools_map eq '["finish", "workflow"]' \
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

- **Emoji reactions, scripted (`S16.reactions`).** A persisted conversation whose agent has only `react` and `unreact`. Turn 1 ("Please look into the build."): the agent reacts `eyes`, tries `rocket`, takes `eyes` back, reacts `white_check_mark`, and finishes. The program then calls `conversation.react("heart")` and `conversation.react("rocket")`; turn 2 ("Thanks, that's all.") finishes, with a recording LLM keeping what each call was sent.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S16/reactions \
    --check status_turn1 eq finished --check status_after_user_react eq finished --check status_turn2 eq finished \
    --check reactions len-eq 4 --check event_before_first_reaction eq ObservationEvent \
    --check reactions.0.source eq agent --check reactions.0.emoji eq eyes --check reactions.0.on_first_message eq true \
    --check reactions.1.emoji eq eyes --check reactions.1.removed eq true --check reactions.1.on_first_message eq true \
    --check reactions.2.emoji eq white_check_mark --check reactions.2.on_first_message eq true \
    --check reactions.3.source eq user --check reactions.3.emoji eq heart --check reactions.3.on_first_finish eq true \
    --check reactions.3.text_ending eq "The build is green." \
    --check observations.0.text eq "Reacted :eyes:." --check observations.1.text eq "Removed :eyes:." \
    --check agent_errors len-eq 1 --check agent_errors.0 contains "Error validating tool 'react'" \
    --check bad_user_emoji contains "Unknown emoji 'rocket'" --check user_reaction_target_is_finish eq true \
    --check agent_reaction_preceded_by len-eq 3 --check agent_reaction_preceded_by.0 eq ObservationEvent \
    --check agent_reaction_preceded_by.1 eq ObservationEvent --check agent_reaction_preceded_by.2 eq ObservationEvent \
    --check turn2_sees_user_heart eq true --check turn2_user_reaction_lines eq 1 --check turn2_sees_own_reaction_line eq false
  ```
- **Emoji reactions with a real model (`S16.reactions`).** Live; the model gets `react` and `unreact` and one message asking it to react `eyes` first and then answer.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk program S16/reactions --arg live \
    --check execution_status eq finished --check react_calls ge 1 \
    --check agent_reactions.0.emoji eq eyes --check agent_reactions.0.on_message eq true
  ```

- **Message edits, scripted (`S16.message-edit-tool`, `S16.message-edit-event`, `S16.message-edit-llm-view`).** A persisted conversation whose agent has only `edit_message`. Turn 1 ("Check the build."): the agent asks "Should I run the lookup on windy?" in a plain message. Turn 2 ("Yes, windy."): it strikes the question, tries to edit the user's "Yes, windy", sends two edits of the question in one response, and finishes "The build is green.". Turn 3 ("One test was flaky."): it strikes that `finish` and finishes again. The program then reacts `heart` to the first `finish`, converts the events with `events_to_messages`, and reloads the conversation, with a recording LLM keeping what each call was sent.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S16/message_edits \
    --check statuses len-eq 3 --check statuses.2 eq finished \
    --check edits len-eq 3 --check edits.0.source eq agent --check edits.0.on_question eq true --check edits.0.after eq ObservationEvent \
    --check edits.0.text eq "~~Should I run the lookup on windy?~~ [Edit: Running it on windy.]" \
    --check edits.1.text eq "~~Should I run the lookup on windy?~~ [Edit: Ran it on windy.]" \
    --check edits.2.on_first_finish eq true --check edits.2.after eq ObservationEvent \
    --check observations.0.is_error eq false --check observations.0.text contains "Edited. The message now reads:" \
    --check observations.1.is_error eq true --check observations.1.text contains "old_str is not in any of your messages" \
    --check observations.2.is_error eq false \
    --check observations.3.is_error eq true --check observations.3.text contains "Not edited: an earlier call in this response already edits this message" \
    --check observations.3.text contains "[Edit: Ran it on windy.]" \
    --check question_written eq "Should I run the lookup on windy?" \
    --check question_reads eq "~~Should I run the lookup on windy?~~ [Edit: Ran it on windy.]" \
    --check reaction_text_ending eq "~~The build is green.~~ [Edit: one test is flaky.]" \
    --check roundtrip_equal eq true --check reloaded_edits eq 3 \
    --check reloaded_question_reads eq "~~Should I run the lookup on windy?~~ [Edit: Ran it on windy.]" \
    --check edit_in_view eq false --check edit_is_llm_convertible eq false \
    --check llm_assistant_has_question eq true --check llm_assistant_has_struck_text eq false --check llm_sees_edit_results eq 3 \
    --check converted_assistant_has_question eq true --check converted_has_struck_text eq false
  ```
- **Message edits with a real model (`S16.message-edit-tool`, `S16.message-edit-event`).** Live; the model asks a question, gets the answer, and is told to strike the question with `edit_message`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk program S16/message_edits --arg live \
    --check execution_status eq finished --check edit_calls ge 1 --check edits ge 1 \
    --check edits_target_agent_messages eq true --check edited_text_has_strike eq true
  ```
- **Conversation mentions without an agent server (`S16.conversation-mention-link`, `S16.conversation-mention-tools`).** The program builds mention links from a fixed id, then a scripted agent with `find_conversations` and `read_conversation` (and `OH_INTERNAL_SERVER_URL` at a closed local port) searches for "export", reads a mention link, and reads "the export one"; finally it gives `find_conversations` a param. The tools against a real server are the agent-server map's F38.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S16/conversation_mentions \
    --check scheme eq "conversation:" \
    --check links.plain eq "[Fix the export button](conversation:12345678123456781234567812345678)" \
    --check links.dashed_str eq "[Fix](conversation:12345678123456781234567812345678)" \
    --check links.hostile eq "[a evil b c d next](conversation:12345678123456781234567812345678)" \
    --check links.blank eq "[untitled conversation](conversation:12345678123456781234567812345678)" \
    --check links.long_len eq 129 \
    --check execution_status eq finished \
    --check observations.0.tool eq find_conversations --check observations.0.is_error eq true \
    --check observations.0.text contains "Could not list conversations:" \
    --check observations.1.is_error eq true --check observations.1.text contains "Could not read conversation 12345678123456781234567812345678:" \
    --check observations.2.is_error eq true --check observations.2.text contains "is not a conversation mention or id" \
    --check read_only.find_conversations eq true --check read_only.read_conversation eq true \
    --check system_prompt_has_guidance eq true \
    --check params_error contains "does not accept parameters"
  ```
- **Ask the user, scripted (`S16.ask-user-tool`, `S16.ask-user-answer`, `S16.ask-user-chat-reply`, `S16.ask-user-holds-completion`).** Agents with only `ask_user`, one conversation per scenario: the agent asks "Delete the old build caches?" (options "Delete them" and "Keep them", recommended 1) and the program reloads it; refuses three bad answers, taps option 1 with words, sends the returned message and runs; answers a question with two "Default" options; relays a message with a `sender`, then writes its own; asks invalid questions and two at once; asks under `AlwaysConfirm`; asks with a Stop hook that denies completion once (sync and async); and returns `ask_user` and `finish` together with a critic that always asks for refinement.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S16/ask_user \
    --check ask.status eq finished --check ask.llm_calls eq 1 --check ask.tool eq ask_user \
    --check ask.action_kind eq AskUserAction --check ask.recommended eq 1 \
    --check ask.observations len-eq 1 --check ask.observations.0.is_error eq false \
    --check ask.observations.0.text contains "Your turn ends here" \
    --check ask.pending_id_is_action eq true --check ask.pending_labels.1 eq "Keep them" --check ask.pending_recommended eq 1 \
    --check ask.pending_after_reload eq true \
    --check answer.wrong_id eq "ValueError: That question is not waiting for an answer." \
    --check answer.missing_option eq "ValueError: The question has 2 options; there is no option 2." \
    --check answer.empty contains "An answer picks an option, gives text, or both." --check answer.events_after_refusals eq 0 \
    --check answer.event_kind eq AskUserAnswerEvent --check answer.event_source eq user --check answer.event_ask_id_matches eq true \
    --check answer.option_index eq 1 --check answer.option_label eq "Keep them" --check answer.text eq "keep them for a week" \
    --check answer.event_parent_is_observation eq true --check answer.still_pending_before_message eq true \
    --check answer.message_text matches '^\[Answer to your question: "Delete the old build caches\?"\]\nOption 2: Keep them\nkeep them for a week$' \
    --check answer.pending_after_message eq false --check answer.status eq finished --check answer.llm_calls eq 2 \
    --check answer.second_tap eq "ValueError: That question is not waiting for an answer." \
    --check labels.message_text contains "Option 2: Default" \
    --check chat.relayed_closes eq false --check chat.own_message_closes eq true --check chat.llm_calls eq 2 \
    --check invalid.one_option.pending eq false --check invalid.one_option.agent_errors.0 contains "List should have at least 2 items" \
    --check invalid.bad_recommended.agent_errors.0 contains "recommended is 5, but there are only 2 options" \
    --check invalid.blank_label.pending eq false --check invalid.blank_label.llm_calls eq 2 \
    --check invalid.blank_label.observations.0.is_error eq true \
    --check invalid.blank_label.observations.0.text contains "Each option needs a nonblank label" \
    --check invalid.two_questions.observations.0.is_error eq false --check invalid.two_questions.observations.1.is_error eq true \
    --check invalid.two_questions.observations.1.text contains "Not shown: this response already ends the turn" \
    --check invalid.two_questions.pending_question eq "Delete the old build caches?" --check invalid.two_questions.llm_calls eq 1 \
    --check confirm.status eq finished --check confirm.pending eq true \
    --check stop_hook.sync.llm_calls_after_ask eq 1 --check stop_hook.sync.pending_after_ask eq true \
    --check stop_hook.sync.hook_ran_over_question eq false --check stop_hook.sync.hook_ran_after_answer eq true \
    --check stop_hook.sync.llm_calls_after_answer eq 3 --check stop_hook.sync.hook_feedback_messages eq 1 \
    --check stop_hook.async.llm_calls_after_ask eq 1 --check stop_hook.async.hook_ran_over_question eq false \
    --check stop_hook.async.hook_ran_after_answer eq true --check stop_hook.async.llm_calls_after_answer eq 3 \
    --check refine.status eq finished --check refine.pending eq true --check refine.llm_calls eq 1 --check refine.user_messages eq 1
  ```
  Asking takes one model call and ends the run; nothing is recorded for a refused answer; after the tap the question stays pending until the returned message is sent. The Stop hook leaves no marker and the agent gets no second call while the question waits; after the answer the hook denies the first completion once (one feedback message, a third call). With refinement on, the question wins and no refinement message is sent.
- **Ask the user with a real model (`S16.ask-user-tool`).** Live; the model gets only `ask_user` and a request to ask which of two file names to use.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk program S16/ask_user --arg live \
    --check execution_status eq finished --check ask_calls eq 1 --check pending eq true --check options ge 2
  ```

## Gotchas

- `tests/integration/run_infer.py --tool-preset planning` (also offered by the integration-runner workflow) pairs the planning tools with task tests that edit files. The planner can write only its plan, so a task test that edits files, such as t01, fails by construction (on 2026-11-03, t01 with DeepSeek: "Typos were not fully corrected"). The map therefore drives the preset with the scripted program above, not through the runner.
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
- `find_conversations` and `read_conversation` take no `server_url` through `Tool(...)`: a program can only steer them with `OH_INTERNAL_SERVER_URL`, and the default `http://127.0.0.1:8000` reaches whatever agent server happens to listen there, sending `OH_SESSION_API_KEYS_0`/`SESSION_API_KEY` from the program's environment.
- A `MessageEditEvent` never changes the message event it targets: `MessageEvent.llm_message` and `FinishAction.message` keep the original text. Read the current text with `message_text(...)`; a client that shows `event.llm_message` shows the unedited message.
- `ask_user` has no executor work: its observation only says the question was shown. The question a client shows is the `ActionEvent`'s `AskUserAction`, and the answer is the user message after it; an `AskUserAnswerEvent` alone (a process that died before the message was saved) leaves the question pending, so the same tap can be sent again.
- `find_pending_ask(events)` expects the active branch (`conversation.state.active_branch()`, under the state lock); given the whole `conversation.state.events` it also sees questions on abandoned branches (#6907 in `run_goal`, S04).
- `AskUserTool.user_selectable` is false: settings UIs that list selectable tools do not offer it; a program adds it by name.
