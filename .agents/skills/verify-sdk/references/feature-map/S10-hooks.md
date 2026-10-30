# Hooks

A Python program passes `Conversation(..., hook_config=HookConfig(...))` and the SDK runs hooks at six lifecycle events: `PreToolUse` (before a tool call, can block it), `PostToolUse` (after the observation), `UserPromptSubmit` (when a user message arrives, can block it or add context), `SessionStart` (when the conversation first initializes), `SessionEnd` (on `close()`), and `Stop` (when the agent tries to finish, can send it back to work). Each event has a list of `HookMatcher`s whose `matcher` selects tools by exact name, `*`, or regex, and each matcher runs `HookDefinition`s of three types. A `command` hook is a shell command that gets the event as JSON on stdin and `OPENHANDS_*` variables in its environment; exit code 2, `{"decision": "deny"}` or `{"continue": false}` on stdout blocks, any other non-zero exit is a non-blocking error, `reason` and `additionalContext` are read from the JSON, and `async: true` fires it in the background. A `prompt` hook asks the conversation's LLM for one completion and expects `{"decision": "allow"|"deny", "reason": ...}`. An `agent` hook runs a short sub-conversation (with optional tools) that ends with the same JSON. Prompt and agent hooks fall open (allow) when there is no LLM, the call fails, or the answer is not a valid decision. Every hook run emits a `HookExecutionEvent`; a blocked tool call becomes a `UserRejectObservation` with `rejection_source="hook"`, a blocked user message ends the run without a model call, and a denied stop injects feedback and keeps running. Plugin hooks (S08) and sub-agent hooks (S11) feed the same machinery.

Source: `openhands-sdk/openhands/sdk/hooks/`, `openhands-sdk/openhands/sdk/event/hook_execution.py`, `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`, `openhands-sdk/openhands/sdk/conversation/state.py`, `openhands-sdk/openhands/sdk/agent/agent.py`

Needs: `llm` (examples; prompt and agent hooks), `git` (example 33 initializes a repo)

Examples: `examples/01_standalone_sdk/33_hooks/main.py`, `examples/01_standalone_sdk/51_agent_hooks/main.py`, `examples/01_standalone_sdk/57_prompt_hooks/main.py`

## Sub-features

- `S10.config-format`: `HookConfig` accepts typed snake_case fields, PascalCase keys (`PreToolUse`) and the legacy `{"hooks": {...}}` wrapper, rejects unknown event names and both spellings of one event, forbids extra fields, round-trips through JSON (with `async` as alias), and `HookConfig.merge` concatenates matchers per event. Unit: `tests/sdk/hooks/test_config.py::TestHookConfig`, `::TestAsyncHooks`. Example: `examples/01_standalone_sdk/33_hooks/main.py` (typed config; `from_dict` shown in comments). Real-LLM: none.
- `S10.definition-validation`: `type="command"` needs `command`; `prompt` needs `prompt` and forbids `command` and `async`; `agent` forbids `command` and `async`; `display_command` labels command-less hooks as `<type>-hook:<name>`. Unit: `tests/sdk/hooks/test_config.py` (`test_command_hook_requires_command`, `test_hook_definition_validation_errors`, `test_display_command`, `test_agent_hook_rejects_async`, `test_prompt_hook_rejects_async`). Example: none. Real-LLM: none.
- `S10.config-discovery`: `HookConfig.load(path)` reads one file; `HookConfig.load(working_dir=...)` looks for `<dir>/.openhands/hooks.json`, then `~/.openhands/hooks.json`; a missing file gives an empty config. Unit: `tests/sdk/hooks/test_config.py::TestHookConfig` (`test_load_from_json_file`, `test_load_missing_file_returns_empty`, `test_load_discovers_config_in_working_dir`). Example: none. Real-LLM: none.
- `S10.matcher`: `*` or an empty matcher matches every tool (and events without a tool), `/regex/` and strings with regex metacharacters are full-matched, anything else must equal the tool name; an invalid regex falls back to an exact match. Unit: `tests/sdk/hooks/test_config.py::TestHookMatcher`, `::TestHookConfig::test_get_hooks_filters_by_tool_name`. Example: `examples/01_standalone_sdk/33_hooks/main.py` (`matcher="terminal"`). Real-LLM: none.
- `S10.command-exec`: a command hook runs through the shell in the workspace with the event JSON on stdin, `OPENHANDS_PROJECT_DIR`, `OPENHANDS_SESSION_ID`, `OPENHANDS_EVENT_TYPE`, `OPENHANDS_TOOL_NAME` in a sanitized environment, captures stdout and stderr, and reports a timeout or a missing command as an error result. Unit: `tests/sdk/hooks/test_executor.py::TestHookExecutor`. Example: `examples/01_standalone_sdk/33_hooks/main.py` (`log_tools.sh`). Real-LLM: none.
- `S10.exit-codes`: exit 0 is success, exit 2 blocks, any other non-zero exit is a non-blocking failure; JSON stdout `decision: deny` or `continue: false` also blocks, a non-string `decision` is ignored; `execute_all` stops at the first block. Unit: `tests/sdk/hooks/test_executor.py::TestHookExecutor` (`test_execute_blocking_exit_code`, `test_execute_json_output_decision`, `test_non_string_decision_is_ignored_rather_than_fatal`, `test_execute_all_stops_on_block`). Example: `examples/01_standalone_sdk/33_hooks/main.py` (`block_dangerous.sh`). Real-LLM: none.
- `S10.async-hooks`: `async: true` command hooks start in their own process group, get stdin, return at once with `async_started`, are killed after their timeout or at `SessionEnd`; an async `PreToolUse` hook logs that it cannot block. Unit: `tests/sdk/hooks/test_executor.py::TestAsyncHookExecution`, `::TestAsyncProcessManager`, `tests/sdk/hooks/test_manager.py::TestAsyncHookManager`. Example: `examples/05_skills_and_plugins/02_loading_plugins/example_plugins/code-quality/hooks/hooks.json` (plugin hook). Real-LLM: none.
- `S10.pre-tool-use-block`: a blocking `PreToolUse` hook records the action in `state.blocked_actions`; the agent then emits a `UserRejectObservation` (`rejection_source="hook"`, the hook's reason) instead of running the tool, and other actions in the same batch still run. Unit: `tests/sdk/hooks/test_integration.py::TestHookEventProcessorBlocking`, `tests/sdk/agent/test_action_batch.py` (`test_prepare_with_blocked`, `test_emit_blocked_produces_rejection`), `tests/sdk/hooks/test_manager.py::TestHookManager::test_run_pre_tool_use_blocks_when_hook_denies`. Example: `examples/01_standalone_sdk/33_hooks/main.py` (demo 2). Real-LLM: none.
- `S10.post-tool-use`: `PostToolUse` hooks get `tool_name`, `tool_input` and the structured `tool_response` of the matching action, looked up from the event log; an error only logs. Unit: `tests/sdk/hooks/test_integration.py::TestPostToolUseActionLookup`, `tests/sdk/hooks/test_manager.py::TestHookManager::test_run_post_tool_use`. Example: `examples/01_standalone_sdk/33_hooks/main.py` (demo 1). Real-LLM: none.
- `S10.user-prompt-context`: `additionalContext` from `UserPromptSubmit` hooks is appended to the message's `extended_content`, reaches the LLM message, and is persisted in the stored event. Unit: `tests/sdk/hooks/test_integration.py::TestAdditionalContextInjection`, `::TestLocalConversationHookCallbackWiring`. Example: `examples/01_standalone_sdk/33_hooks/main.py` (demo 3, `inject_git_context.sh`). Real-LLM: none.
- `S10.user-prompt-block`: a blocking `UserPromptSubmit` hook records the message in `state.blocked_messages`; the next agent step pops it and finishes the run without calling the LLM. Unit: `tests/sdk/hooks/test_integration.py::TestUserPromptSubmitBlocking`, `tests/sdk/test_agent_step_bounded_scan.py`. Example: none. Real-LLM: none.
- `S10.blocked-state-persistence`: `blocked_actions` and `blocked_messages` survive a persisted conversation's resume, and removals persist too. Unit: `tests/sdk/hooks/test_integration.py::TestBlockedStatePersistence`, `tests/sdk/conversation/local/test_state_serialization.py`. Example: none. Real-LLM: none.
- `S10.stop-hook`: when the agent finishes, `Stop` hooks run; a deny (exit 2 or `decision: deny`) injects the hooks' `additionalContext` (or the reason) as an environment message and sets the conversation back to running; a hook error allows the stop. Unit: `tests/sdk/hooks/test_integration.py::TestStopHookIntegration`, `::TestStopHookConversationIntegration::test_stop_hook_denial_injects_feedback_and_continues`, `tests/sdk/hooks/test_manager.py::TestHookManager::test_run_stop_blocked_means_continue`. Example: `examples/01_standalone_sdk/33_hooks/main.py` (`require_summary.sh`), `examples/01_standalone_sdk/51_agent_hooks/main.py` (quality reviewer). Real-LLM: none.
- `S10.session-start`: `SessionStart` hooks run once when the conversation first initializes (first `send_message()`/`run()`), and emit a `HookExecutionEvent`. Unit: `tests/sdk/hooks/test_integration.py::TestHookExecutionEventEmission::test_hook_execution_event_emitted_for_session_start`, `tests/sdk/hooks/test_manager.py::TestHookManager::test_run_session_start`. Example: none. Real-LLM: none.
- `S10.session-end-on-close`: `conversation.close()` runs `SessionEnd` hooks, then kills leftover async hook processes. Unit: **none (untested)** at the conversation level; `tests/sdk/hooks/test_manager.py::TestAsyncHookManager::test_session_end_runs_hooks_before_cleanup` covers only `HookManager.run_session_end`. Example: none. Real-LLM: none.
- `S10.execution-events`: each hook run emits a `HookExecutionEvent` (`hook_event_type`, `hook_command`, `tool_name`, `success`, `blocked`, `exit_code`, `stdout`, `stderr`, `reason`, `additional_context`, `error`, `action_id` or `message_id`, `hook_input`) unless `emit_hook_events=False`. Unit: `tests/sdk/hooks/test_integration.py::TestHookExecutionEventEmission`. Example: `examples/01_standalone_sdk/51_agent_hooks/main.py` (prints them). Real-LLM: none.
- `S10.prompt-hook`: a `type="prompt"` hook sends the policy and the event as separate messages to a non-streaming copy of the conversation's current LLM (`usage_id` `prompt-hook:<name>`, the hook's timeout), parses the first JSON object of the reply, falls open on a bad answer or failure, and merges its cost into the parent's stats. Unit: `tests/sdk/hooks/test_executor.py::TestPromptHookExecution`, `tests/sdk/hooks/test_manager.py::TestHookManager::test_pre_tool_use_prompt_deny_blocks`. Example: `examples/01_standalone_sdk/57_prompt_hooks/main.py`. Real-LLM: none.
- `S10.agent-hook`: a `type="agent"` hook runs a `LocalConversation` with the hook's `system_prompt`, the listed `tools` plus `FinishTool`, `max_iterations`, no hooks of its own, and a sub-visualizer; the final response is parsed like a prompt hook (fenced or prose-wrapped JSON accepted), failures fall open, and metrics land under `agent-hook:<name>` in the parent's stats. Unit: `tests/sdk/hooks/test_executor.py::TestAgentHookExecution`, `tests/sdk/hooks/test_manager.py::TestHookManager::test_stop_prompt_deny_keeps_agent_running`. Example: `examples/01_standalone_sdk/51_agent_hooks/main.py`. Real-LLM: none.
- `S10.llm-follows-switch`: prompt and agent hooks read the conversation's LLM at call time, so `switch_llm()`/`switch_profile()` changes the model they use. Unit: `tests/sdk/hooks/test_executor.py::TestAgentHookExecution::test_llm_getter_is_resolved_live`, `::TestPromptHookExecution::test_llm_getter_is_resolved_live`. Example: none. Real-LLM: none.
- `S10.plugin-hooks-merge`: hooks from attached, ambient or runtime-loaded plugins run after the explicit `hook_config`, and the merged config is stored in `conversation.state.hook_config`; loading a plugin later restarts the hook processor. Unit: `tests/sdk/conversation/test_local_conversation_plugins.py::TestLocalConversationPlugins` (`test_plugin_hooks_combined_with_explicit_hooks`, `test_load_plugin_merges_runtime_hooks_and_restarts_processor`), `tests/sdk/plugin/test_plugin_loader.py::TestHookConfigMerge`. Example: `examples/05_skills_and_plugins/02_loading_plugins/main.py`. Real-LLM: none.

## How to get to it (agent POV)

- `Conversation(agent=..., workspace=..., hook_config=HookConfig(pre_tool_use=[HookMatcher(matcher="terminal", hooks=[HookDefinition(command="./check.sh", timeout=10)])], post_tool_use=[...], user_prompt_submit=[...], session_start=[...], session_end=[...], stop=[...]))`. All classes import from `openhands.sdk.hooks` (`HookConfig`, `HookMatcher`, `HookDefinition`, `HookType`, `HookManager`, `HookExecutor`, `HookResult`, `HookEvent`, `HookEventType`, `HookDecision`, `create_hook_callback`). Covers every conversation-level sub-feature.
- `HookConfig.from_dict({...})` / `HookConfig.load(path=None, working_dir=...)` to read a `hooks.json`; the conversation does not do this for you. `S10.config-format`, `S10.config-discovery`.
- `HookDefinition(type=HookType.PROMPT, name=..., prompt=..., timeout=...)` and `HookDefinition(type=HookType.AGENT, name=..., system_prompt=..., tools=["file_editor"], max_iterations=3, timeout=...)`. `S10.prompt-hook`, `S10.agent-hook`.
- Without a conversation: `HookManager(config=..., working_dir=..., session_id=..., llm=..., conversation_stats=...)` with `run_pre_tool_use(tool_name=, tool_input=)`, `run_post_tool_use`, `run_user_prompt_submit(message=)`, `run_session_start`, `run_session_end`, `run_stop(reason=)`. Used by example 57.
- Callback events: `HookExecutionEvent` (`openhands.sdk.event`) through `Conversation(callbacks=[...])` or `conversation.state.events`; blocked work in `conversation.state.blocked_actions` / `blocked_messages`; rejections as `UserRejectObservation(rejection_source="hook")`.
- Files on disk: `<workspace>/.openhands/hooks.json`, `~/.openhands/hooks.json` (via `HookConfig.load` only), plugin `hooks/hooks.json` or `dev.openhands/hooks/hooks.json` (S08), sub-agent frontmatter `hooks:` (S11).
- Env vars seen by command hooks: `OPENHANDS_PROJECT_DIR`, `OPENHANDS_SESSION_ID`, `OPENHANDS_EVENT_TYPE`, `OPENHANDS_TOOL_NAME`; examples read `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL`.

## Driving it

Preconditions:
- `uv sync --dev` has run; `control-sdk` is on `PATH` (`export PATH="$PWD/.agents/skills/verify-sdk/scripts:$PATH"`). `sdk-map run` does both.
- `bash` and `tmux` on `PATH`: the hook scripts in `programs/S10/hooks/` are bash, and the conversations use `TerminalTool`.
- Live bullets: `DEEPSEEK_API_KEY` exported; `git` on `PATH` for example 33.

- **Command hooks at every lifecycle event (`S10.config-discovery`, `S10.command-exec`, `S10.matcher`, `S10.exit-codes`, `S10.async-hooks`, `S10.pre-tool-use-block`, `S10.post-tool-use`, `S10.user-prompt-context`, `S10.stop-hook`, `S10.session-start`, `S10.session-end-on-close`, `S10.execution-events`).** The program writes `<workspace>/.openhands/hooks.json` (legacy wrapper, PascalCase keys) pointing at the scripts in `programs/S10/hooks/`, loads it with `HookConfig.load(working_dir=...)` and passes it to a persisted conversation. The scripted agent runs `rm -rf build` and a `file_editor create` in one response, tries to finish, creates `done.txt`, and finishes; the program closes the conversation and reloads the saved events.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S10/hooked_run \
    --check execution_status eq finished \
    --check build_kept eq true --check notes_written eq true \
    --check rejections eq 1 --check rejection_source eq hook --check rejection_reason contains "rm -rf is blocked" \
    --check pre_terminal_blocked.0 eq true --check pre_terminal_exit_codes.0 eq 2 --check pre_terminal_action_ids_set eq true \
    --check pre_file_calls eq 1 \
    --check env_event_type eq PreToolUse --check env_tool_name eq terminal \
    --check env_project_dir_is_workspace eq true --check env_session_id_is_conversation eq true --check cwd_is_workspace eq true \
    --check post_tools contains file_editor --check post_has_tool_response eq true --check post_file_editor_input_command eq create \
    --check soft_fail_exit_codes.0 eq 1 --check soft_fail_blocked eq false --check soft_fail_success eq false --check soft_fail_stderr eq "soft failure" \
    --check async_got_stdin eq PostToolUse --check async_alive_after_close eq false \
    --check user_extended_content.0 eq "HOOK-CONTEXT: the build is green" --check llm_saw_context eq true \
    --check stop_blocked.0 eq true --check stop_blocked.1 eq false \
    --check stop_feedback.0 contains "Create done.txt first." --check llm_calls eq 4 \
    --check hook_events_by_type.SessionStart eq 1 --check session_start_log_lines eq 1 \
    --check hook_events_by_type.SessionEnd eq 1 --check session_ended_file eq true \
    --check hook_event_fields contains hook_input --check hook_event_fields contains additional_context \
    --check stored_hook_config_events len-eq 6
  ```
  The blocked `rm -rf` becomes a `UserRejectObservation` with `rejection_source="hook"` while the `file_editor` call of the same response still runs; the `/file_.*/` matcher fires only for `file_editor`; the exit-1 hook is a logged failure that blocks nothing; the async hook got the event on stdin and its `sleep` is gone once `close()` ran `SessionEnd`; the hook's `additionalContext` is in the saved user message and in the first LLM request; the first finish is sent back with `[Stop hook feedback] Create done.txt first.`.
- **A denied prompt across a resume (`S10.user-prompt-block`, `S10.*-state-persistence`).** A `UserPromptSubmit` hook (`deny_secret_prompt.sh`, exit 2) blocks a message; the program closes, reopens the persisted conversation with a model that has nothing scripted (any LLM call would raise), runs it, and reopens once more. An action id the program marks with `state.block_action(...)` rides along to show `blocked_actions` persists too.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S10/blocked_prompt \
    --check blocked_after_send eq 1 --check block_reason eq "asking for the secret is not allowed" \
    --check blocked_after_reopen eq 1 --check same_message_blocked eq true --check action_block_after_reopen eq true \
    --check run_error missing --check execution_status eq finished --check agent_messages len-eq 0 \
    --check blocked_after_run eq 0 --check action_block_after_pop eq false \
    --check user_prompt_hook_blocked.0 eq true --check hook_message_id_set eq true
  ```
- **Config forms, validation, discovery, matchers and exit codes (`S10.config-format`, `S10.definition-validation`, `S10.config-discovery`, `S10.matcher`, `S10.exit-codes`, `S10.command-exec`).** Through the public classes, without a conversation (as example 57 uses `HookManager`): typed, PascalCase and legacy-wrapper configs, `async` alias and merge; the user `hooks.json` under `OH_PERSISTENCE_DIR`; each kind of command-hook answer.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S10/config_forms \
    --check typed_pre_tool_use eq 1 --check pascal_pre_tool_use eq 1 --check legacy_events len-eq 2 \
    --check unknown_event_rejected eq true --check both_spellings_rejected eq true --check extra_field_rejected eq true \
    --check async_alias eq true --check async_dumps_as.0 eq async --check async_roundtrip eq true \
    --check merged_pre_tool_use eq 2 --check merged_stop eq 1 \
    --check command_requires_command eq true --check prompt_requires_prompt eq true --check prompt_forbids_command eq true \
    --check prompt_forbids_async eq true --check agent_forbids_command eq true --check agent_forbids_async eq true \
    --check display_prompt eq prompt-hook:policy --check display_agent eq agent-hook:review --check display_command eq ./check.sh \
    --check missing_is_empty eq true --check user_file_found eq true --check project_wins eq true --check explicit_path eq true \
    --check match.star_any eq true --check match.empty_any eq true --check match.star_no_tool eq true \
    --check match.exact_no_tool eq false --check match.exact eq true --check match.exact_other eq false \
    --check match.slash_regex eq true --check match.auto_regex eq true --check match.full_match_only eq false \
    --check match.bad_regex_exact eq true \
    --check exit0.continue eq true --check exit0.success eq true \
    --check exit2.continue eq false --check exit2.blocked eq true \
    --check exit1.continue eq true --check exit1.blocked eq false --check exit1.success eq false \
    --check json_deny.continue eq false --check json_deny.reason eq policy \
    --check json_continue_false.continue eq false --check json_non_string_decision.continue eq true \
    --check missing_command.success eq false --check missing_command.exit_code eq 127 --check missing_command.continue eq true \
    --check chain_continue eq false --check chain_results eq 1 --check chain_second_ran eq false \
    --check timeout_error contains "timed out" --check timeout_success eq false
  ```
- **Prompt and agent hooks with a scripted model (`S10.prompt-hook`, `S10.agent-hook`, `S10.llm-follows-switch`).** A prompt hook on `PreToolUse(terminal)` and an agent hook on `Stop` answer from the conversation's own scripted LLM (hook copies share the script queue): a JSON deny blocks `ls`, a prose answer falls open, a fenced-JSON deny from the agent hook keeps the agent running, prose plus JSON allows it to stop. After `conversation.switch_llm(...)` the prompt hook answers from the new model.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S10/llm_hooks \
    --check first_status eq finished --check first_script_left eq 0 --check execution_status eq finished \
    --check pre_commands.0 eq prompt-hook:policy \
    --check pre_blocked.0 eq true --check pre_success.0 eq true \
    --check pre_blocked.1 eq false --check pre_success.1 eq false --check pre_errors.1 contains "no parseable JSON" \
    --check rejection_reasons.0 eq "listing files is not allowed" --check fall_open_ran eq true \
    --check stop_commands.0 eq agent-hook:reviewer --check stop_blocked.0 eq true --check stop_reasons.0 eq "write REPORT.md" \
    --check stop_blocked.1 eq false --check stop_feedback.0 eq "[Stop hook feedback] write REPORT.md" \
    --check rejection_reasons.1 eq "the switched model says no" --check switched_script_left eq 0 \
    --check usage_ids contains prompt-hook:policy
  ```
  The agent hook's own metrics (`agent-hook:<name>`) need a real `LLM`: the SDK's stats registry skips `TestLLM` (see Gotchas), so that part is proven by example 51 below.
- **Plugin hooks next to explicit ones (`S10.plugin-hooks-merge`).** An explicit `PostToolUse` hook, a plugin attached with `plugins=[PluginSource(...)]` and, after the first run, a plugin loaded with `load_plugin("runtime-hooks@local")` from the local marketplace in `programs/S10/marketplace/`; each hook appends its name to `order.log`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S10/plugin_hooks \
    --check execution_status eq finished \
    --check order_after_first_run.0 eq start-attached --check order_after_first_run.1 eq explicit --check order_after_first_run.2 eq attached \
    --check post_tool_use_matchers_before_load eq 2 --check post_tool_use_matchers_after_load eq 3 \
    --check order.-3 eq explicit --check order.-2 eq attached --check order.-1 eq runtime \
    --check resolved_plugins len-eq 2
  ```
- **Command hooks with a real model, example 33 (`S10.command-exec`, `S10.pre-tool-use-block`, `S10.post-tool-use`, `S10.user-prompt-context`, `S10.stop-hook`).** Live.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output "Example Complete!" --expect-output "EXAMPLE_COST" -- python examples/01_standalone_sdk/33_hooks/main.py
  ```
  Demo 1 prints a `tool_usage.log` line for the `echo`; demo 2's `rm -rf /tmp/test` is rejected by `block_dangerous.sh`; demo 3 gets the git status injected and cannot finish until `summary.txt` exists.
- **Agent hooks with a real model, example 51 (`S10.agent-hook`, `S10.stop-hook`, `S10.execution-events`).** Live.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output "Example Complete!" --expect-output "EXAMPLE_COST" -- python examples/01_standalone_sdk/51_agent_hooks/main.py
  ```
  Demo 1 prints `[hook] PreToolUse DENY  -> agent-hook:security-reviewer` for the `awk '{print}' /etc/passwd` call; demo 2 prints `Stop` lines from `agent-hook:quality-reviewer` and a `REPORT.md` preview.
- **Prompt hooks with a real model, example 57 (`S10.prompt-hook`).** Live; the script asserts both verdicts, so a model that disagrees makes it exit non-zero.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output "ALLOW" --expect-output "DENY" -- python examples/01_standalone_sdk/57_prompt_hooks/main.py
  ```

## Gotchas

- The SDK never loads `.openhands/hooks.json` on its own: without `hook_config` (or a plugin with hooks) a `LocalConversation` creates no hook processor and runs no hook, even when the workspace has that file. `HookManager(config=None)` does auto-discover, but the conversation never builds one without a config.
- Hooks are wired in `_ensure_plugins_loaded()`, so `SessionStart` runs on the first `send_message()`/`run()`, not at construction.
- `conversation.state.hook_config` is stored for observability; reopening a persisted conversation does not rebuild hooks from it. Pass `hook_config` again on resume.
- Only exit code 2 blocks. A hook that exits 1 on a policy violation lets the tool run; this matches the Claude Code contract and is a common mistake.
- `async: true` hooks cannot block, even on `PreToolUse`, and their output is discarded.
- Prompt and agent hooks fall open: no LLM, an LLM error, a timeout, or a reply without a valid `decision` all allow the action (`success=False`, `decision=ALLOW`). Check `HookResult.error` or the event's `success` to tell a real allow from a fall-open.
- Agent hooks spend tokens on every matching event (one sub-conversation per call), and run synchronously before the tool; a broad matcher on `PreToolUse` multiplies cost and latency.
- `examples/01_standalone_sdk/51_agent_hooks/main.py` and `57_prompt_hooks/main.py` default `LLM_MODEL` to `anthropic/claude-sonnet-4-5-20250929`, unlike the other examples (`gpt-5.5`).
- `tests/sdk/test_agent_step_bounded_scan.py` passes but takes about 120 s on this machine; budget for it when running the hook suite under a timeout.
- `conversation.load_plugin(...)` rebuilds the hook processor and runs `SessionStart` again for every configured hook (explicit and earlier plugins too), not only the new plugin's: `programs/S10/plugin_hooks.py` sees `start-attached` twice.
- `PostToolUse` hooks with matcher `*` also run for the `finish` tool's observation.
- `LocalConversation` registers only plain `LLM` objects in its stats registry (`AgentBase.get_all_llms` skips subclasses), so with a scripted `TestLLM` neither `agent` nor `agent-hook:<name>` shows up in `conversation_stats.usage_to_metrics`; prompt-hook metrics are merged directly and do show up. Prove agent-hook spend with a real model.
- `control-sdk exec --expect-output` looks only at the last 1500 characters of stdout; pick strings the examples print at the end.
