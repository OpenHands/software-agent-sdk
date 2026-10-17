# Local conversation lifecycle

A Python program calls `Conversation(agent=..., workspace=...)` with a local workspace (a path string or `LocalWorkspace`) and gets a `LocalConversation` that runs the agent in the same process. Around the step loop (family S01) it owns the conversation's lifecycle: a stable `conversation.id`, optional on-disk persistence under `persistence_dir/<id.hex>/` (`base_state.json` plus one `events/event-NNNNN-<event_id>.json` per event) that a second `Conversation(..., persistence_dir=..., conversation_id=same_id)` reloads and continues, `pause()` from another thread, `arun()` on an asyncio loop with `interrupt()` cancelling mid-LLM-call, `update_secrets(...)` that inject values as environment variables into terminal commands and mask them in output, a pluggable visualizer (Rich console output by default, `visualizer=None` for silence), `fork()` into an independent copy or a branch from a chosen event, and `close()`. Helpers next to it read results out of a finished conversation: `get_agent_final_response(events)`, `conversation.conversation_stats` (per-`usage_id` LLM metrics), `generate_title()`, and `run_goal(...)`, which re-prompts the agent until a second judge LLM agrees an objective is complete.

Source: `openhands-sdk/openhands/sdk/conversation/conversation.py`, `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`, `openhands-sdk/openhands/sdk/conversation/base.py`, `openhands-sdk/openhands/sdk/conversation/state.py`, `openhands-sdk/openhands/sdk/conversation/persistence_const.py`, `openhands-sdk/openhands/sdk/conversation/cancellation.py`, `openhands-sdk/openhands/sdk/conversation/secret_registry.py`, `openhands-sdk/openhands/sdk/conversation/visualizer/`, `openhands-sdk/openhands/sdk/conversation/goal/`, `openhands-sdk/openhands/sdk/conversation/response_utils.py`, `openhands-sdk/openhands/sdk/conversation/conversation_stats.py`, `openhands-sdk/openhands/sdk/conversation/title_utils.py`, `openhands-sdk/openhands/sdk/conversation/types.py`, `openhands-sdk/openhands/sdk/secret/`, `openhands-sdk/openhands/sdk/utils/async_utils.py`

Needs: `llm`, `tmux`, `network`, `uvx`

Examples: `examples/01_standalone_sdk/06_interactive_terminal_w_reasoning.py`, `examples/01_standalone_sdk/09_pause_example.py`, `examples/01_standalone_sdk/10_persistence.py`, `examples/01_standalone_sdk/11_async.py`, `examples/01_standalone_sdk/12_custom_secrets.py`, `examples/01_standalone_sdk/26_custom_visualizer.py`, `examples/01_standalone_sdk/48_conversation_fork.py`, `examples/01_standalone_sdk/50_async_cancellation.py`, `examples/01_standalone_sdk/54_goal_completion_loop.py`

## Sub-features

- `S04.factory`: `Conversation(...)` returns a `LocalConversation` for a path or `LocalWorkspace` and a `RemoteConversation` for a `RemoteWorkspace`; `persistence_dir` is only accepted on the local branch. Unit: `tests/sdk/conversation/test_conversation_factory.py`. Example: `examples/01_standalone_sdk/10_persistence.py`. Real-LLM: every `tests/integration/tests/t*.py` builds a `LocalConversation` through `tests/integration/base.py`.
- `S04.conversation-id`: each conversation gets a unique UUID, a passed `conversation_id` is kept and persisted, and the id pins the LLM prompt-cache key. Unit: `tests/sdk/conversation/local/test_conversation_id.py`. Example: `examples/01_standalone_sdk/10_persistence.py`. Real-LLM: none.
- `S04.persist-events`: with `persistence_dir`, every event is written as `events/event-NNNNN-<id>.json` and base state as `base_state.json` under `<persistence_dir>/<id.hex>/`, incrementally; on load, an empty, invalid or missing event file makes the reload fail with an error naming the file or event. Unit: `tests/sdk/conversation/local/test_state_serialization.py`, `tests/sdk/conversation/local/test_conversation_core.py`, `tests/cross/test_persistence_dir_call_sites.py`. Example: `examples/01_standalone_sdk/10_persistence.py`. Real-LLM: none.
- `S04.resume`: a new `Conversation` with the same `persistence_dir` and `conversation_id` reloads the events and state and continues the dialogue with full memory. Unit: `tests/sdk/conversation/local/test_state_serialization.py::test_conversation_state_persistence_save_load`, `tests/cross/test_conversation_restore_behavior.py::test_conversation_restore_lifecycle_happy_path`. Example: `examples/01_standalone_sdk/10_persistence.py`. Real-LLM: none.
- `S04.resume-agent-compat`: on resume, removing tools, removing default tools or changing the agent class fails; adding tools and changing the LLM, condenser or skills succeeds; security risk and summaries survive. Unit: `tests/cross/test_conversation_restore_behavior.py`. Example: none. Real-LLM: none.
- `S04.custom-file-store`: an injected `FileStore` takes precedence over `persistence_dir` for where state is written, while `persistence_dir` is still reported. Unit: `tests/sdk/conversation/local/test_custom_file_store.py`. Example: none. Real-LLM: none.
- `S04.callbacks`: `callbacks=[...]` receive every event after the default callback has persisted it; a persistence failure blocks downstream callbacks; the visualizer runs in a fixed order relative to user callbacks. Unit: `tests/sdk/conversation/local/test_conversation_default_callback.py`, `tests/sdk/conversation/local/test_conversation_visualize_param.py::test_conversation_callback_order`. Example: `examples/01_standalone_sdk/10_persistence.py`. Real-LLM: none.
- `S04.run-error`: an exception inside `run()` surfaces as `ConversationRunError` that names the conversation id, the persistence dir and the issue URL, and emits one `ConversationErrorEvent` (no duplicate when the agent already emitted one). Unit: `tests/sdk/conversation/local/test_run_exception_includes_conversation_id.py`. Example: none. Real-LLM: none.
- `S04.close`: `close()` closes tool executors (terminal sessions), is idempotent, unregisters its atexit hook, and still closes the executors with `delete_on_close=False` (persisted files stay either way); `__del__` also closes. Unit: `tests/sdk/conversation/test_atexit_cleanup.py`, `tests/tools/terminal/test_conversation_cleanup.py`, `tests/sdk/conversation/local/test_conversation_core.py::test_close_propagates_agent_failure_and_allows_retry`. Example: none. Real-LLM: none.
- `S04.pause`: `pause()` from another thread moves RUNNING or IDLE to PAUSED, emits exactly one `PauseEvent`, takes effect between steps (not mid-LLM-call), and a later `run()` resumes. Unit: `tests/sdk/conversation/local/test_conversation_pause_functionality.py`, `tests/sdk/conversation/local/test_agent_status_transition.py::test_execution_status_transitions_to_running_from_paused`. Example: `examples/01_standalone_sdk/09_pause_example.py`. Real-LLM: `tests/integration/tests/c04_token_condenser.py` and `c05_size_condenser.py` call `pause()`.
- `S04.async-callback`: `AsyncCallbackWrapper(coro, loop)` lets a synchronous `run()` in an executor thread deliver events to a coroutine on the main loop. Unit: `tests/sdk/utils/test_async_utils.py`. Example: `examples/01_standalone_sdk/11_async.py`. Real-LLM: none.
- `S04.arun-interrupt`: `await conversation.arun()` uses the async LLM path; `interrupt()` (safe from a signal handler or another thread) cancels it at once, even mid-LLM-call, sets PAUSED, emits an `InterruptEvent`, does not raise `CancelledError`, and the conversation can be resumed; with no `arun()` in flight it falls back to `pause()`. Unit: `tests/sdk/conversation/test_interrupt.py`. Example: `examples/01_standalone_sdk/50_async_cancellation.py`. Real-LLM: none.
- `S04.cancel-token`: each `run()`/`arun()` gets a fresh `CancellationToken` that `interrupt()` sets before cancelling, so pending parallel tool calls are skipped. Unit: `tests/sdk/conversation/test_interrupt.py::test_interrupt_sets_cancel_token`, `::test_execute_batch_skips_all_on_pre_cancelled_token`. Example: `examples/01_standalone_sdk/50_async_cancellation.py`. Real-LLM: none.
- `S04.secrets-update`: `update_secrets({...})` accepts plain strings and `SecretSource` objects (resolved on first use, which is the first command output the conversation masks, then cached), overwrites existing keys, and exports referenced keys as environment variables for terminal commands. Unit: `tests/sdk/conversation/test_secrets_manager.py`, `tests/sdk/conversation/test_secret_source.py`. Example: `examples/01_standalone_sdk/12_custom_secrets.py`. Real-LLM: none.
- `S04.secrets-constructor`: `Conversation(secrets={...})` seeds the same registry at construction, for static and callable values. Unit: `tests/sdk/conversation/test_conversation_secrets_constructor.py`. Example: none. Real-LLM: none.
- `S04.secrets-masking`: secret values echoed by a command are replaced by `<secret-hidden>` in observations and never reach the persisted events. Unit: `tests/tools/terminal/test_secrets_masking.py`, `tests/cross/test_agent_secrets_integration.py`. Example: `examples/01_standalone_sdk/12_custom_secrets.py`. Real-LLM: none.
- `S04.visualizer-default`: by default a `DefaultConversationVisualizer` prints each event as a Rich panel; `visualizer=None` turns it off; a class or instance can be passed. Unit: `tests/sdk/conversation/local/test_conversation_visualize_param.py`. Example: `examples/01_standalone_sdk/10_persistence.py`. Real-LLM: none.
- `S04.visualizer-custom`: a `ConversationVisualizerBase` subclass passed as `visualizer=` receives every event through `on_event`; each event type has its own `visualize` rendering. Unit: `tests/sdk/conversation/test_visualizer.py`. Example: `examples/01_standalone_sdk/26_custom_visualizer.py`. Real-LLM: none.
- `S04.fork`: `fork()` deep-copies the conversation under a new id (or `conversation_id=`), starts IDLE, leaves the source untouched, resets metrics unless `reset_metrics=False`, accepts `agent=`, `title=` and `tags=`, shares the workspace, and persists so the fork survives a reload. Unit: `tests/sdk/conversation/local/test_fork.py`. Example: `examples/01_standalone_sdk/48_conversation_fork.py`. Real-LLM: none.
- `S04.fork-branch`: events carry parent ids forming a tree; `fork(from_event_id=...)` copies only that branch, and `navigate_to(event_id)` moves HEAD so the next event starts a sibling branch (`None` empties the active branch). Unit: `tests/sdk/conversation/local/test_conversation_tree.py`. Example: `examples/01_standalone_sdk/48_conversation_fork.py`. Real-LLM: none.
- `S04.goal-loop`: `run_goal(conversation, objective, judge_llm, max_iterations=N)` sends the objective, runs, asks the judge LLM for a verdict, re-prompts with the missing items, and returns a `GoalOutcome` with status `"complete"` or `"capped"`, the iteration count and the verdict. Unit: `tests/sdk/conversation/goal/`. Example: `examples/01_standalone_sdk/54_goal_completion_loop.py`. Real-LLM: none.
- `S04.interactive-terminal`: with `Tool(name=TerminalTool.name, params={"no_change_timeout_seconds": 3})` the agent can start `python3`, feed it input and exit; an `LLMConvertibleEvent` callback collects the raw LLM messages. Unit: `tests/tools/terminal/test_terminal_session.py::test_python_interactive_input`, `::test_interactive_command`. Example: `examples/01_standalone_sdk/06_interactive_terminal_w_reasoning.py`. Real-LLM: `tests/integration/tests/t07_interactive_commands.py`.
- `S04.final-response`: `get_agent_final_response(conversation.state.events)` returns the text of whichever came last of the agent's `finish` call and its plain-text `MessageEvent`, else `""`. Unit: `tests/sdk/conversation/test_agent_final_response.py`. Example: none. Real-LLM: `tests/integration/tests/t08_image_file_viewing.py`.
- `S04.stats`: `conversation.conversation_stats` keeps `Metrics` per LLM `usage_id`, combines them with `get_combined_metrics()`, restores them on resume, and stats update events carry a snapshot rather than the full history. Unit: `tests/sdk/conversation/test_conversation_stats.py`, `tests/sdk/conversation/test_stats_update_event_snapshot.py`. Example: `examples/01_standalone_sdk/11_async.py` (prints cost). Real-LLM: none.
- `S04.title-and-tags`: `generate_title(llm=None, max_length=50)` asks an LLM (the agent's by default) for a title from the first user message, falls back to truncation on error, and strips reasoning text; `tags={...}` keys must be lowercase alphanumeric. Unit: `tests/sdk/conversation/test_generate_title.py`, `tests/sdk/conversation/test_tags.py`. Example: `examples/01_standalone_sdk/48_conversation_fork.py` (`title=`). Real-LLM: none.

## How to get to it (agent POV)

- `Conversation(agent, workspace=..., persistence_dir=None, conversation_id=None, callbacks=None, visualizer=DefaultConversationVisualizer, secrets=None, delete_on_close=True, tags=None, ...)` in `openhands.sdk`: `S04.factory`, `S04.conversation-id`, `S04.persist-events`, `S04.resume`, `S04.resume-agent-compat`, `S04.callbacks`, `S04.visualizer-*`, `S04.secrets-constructor`, `S04.title-and-tags`. The step-loop arguments (`max_iteration_per_run`, `stuck_detection*`, `max_budget_per_run`) belong to S01.
- On disk: `<persistence_dir>/<conversation_id.hex>/base_state.json` and `.../events/event-NNNNN-<event_id>.json` (`persistence_const.py`): `S04.persist-events`, `S04.resume`. `ConversationState.create(..., file_store=...)` / `LocalConversation(..., file_store=...)`: `S04.custom-file-store`.
- `conversation.run()`, `conversation.pause()`, `await conversation.arun()`, `conversation.interrupt()`, `conversation.cancel_token`: `S04.pause`, `S04.arun-interrupt`, `S04.cancel-token`. `openhands.sdk.utils.async_utils.AsyncCallbackWrapper`: `S04.async-callback`.
- `conversation.update_secrets({...})`, `openhands.sdk.secret.SecretSource` (subclass with `get_value()`), `StaticSecret`, `LookupSecret`: `S04.secrets-*`.
- `openhands.sdk.conversation.visualizer.ConversationVisualizerBase`, `DefaultConversationVisualizer`: `S04.visualizer-*`.
- `conversation.fork(conversation_id=, agent=, title=, tags=, reset_metrics=True, from_event_id=)`, `conversation.navigate_to(event_id)`: `S04.fork`, `S04.fork-branch`.
- `openhands.sdk.conversation.goal.run_goal`, `GoalController`, `judge_goal`: `S04.goal-loop`.
- `openhands.sdk.conversation.response_utils.get_agent_final_response`: `S04.final-response`. `conversation.conversation_stats` / `conversation.state.stats`: `S04.stats`. `conversation.generate_title()`: `S04.title-and-tags`. `conversation.close()`: `S04.close`.
- Env: `LLM_API_KEY` (required), `LLM_MODEL` (default `gpt-5.5`; `anthropic/claude-sonnet-4-5-20250929` in example 50), `LLM_BASE_URL`; example 50 also reads `AUTO_CANCEL_SECONDS` (default 8).

## Driving it

Preconditions:
- `uv sync --dev` has run; `control-sdk` is on `PATH` (`export PATH="$PWD/.agents/skills/verify-sdk/scripts:$PATH"`). `sdk-map run` does both.
- `tmux` on `PATH` (the conversations use `TerminalTool`).
- Live bullets: `DEEPSEEK_API_KEY` exported. Example 10 also needs `uvx` and outbound network (it starts `mcp-server-fetch`).

Scripted bullets use the SDK's `TestLLM` (`--script`, or `make_llm(script=...)` in the `programs/S04/` programs), so the real conversation, real tools and real files on disk run with no key. Where cost or the request the model receives matters, the program points a priced `LLM` at `programs/S02/fake_provider.py`. Every `control-sdk conversation` command reopens the conversation from `persistence_dir` (the run's `conversations/`), so each command after `new` is itself a resume.

- **Factory and ids (`S04.factory`, `S04.conversation-id`).** A path and a `LocalWorkspace` both give a `LocalConversation`, ids are fresh UUID4s, a chosen id is kept and written to `base_state.json`, `persistence_dir` with a `RemoteWorkspace` is refused, and the id is the prompt-cache key the LLM sends.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/lifecycle --arg factory \
    --check path_type eq LocalConversation --check local_workspace_type eq LocalConversation \
    --check ids_differ eq true --check id_is_uuid4 eq true --check chosen_id_kept eq true \
    --check persisted_id exists --check remote_with_persistence_dir contains "persistence_dir should not be set"
  control-sdk program S03/switching --arg profile --check prompt_cache_key_is_conversation_id eq true
  ```
- **The on-disk layout, written event by event (`S04.persist-events`, `S04.resume`, `S04.callbacks`).** Through `control-sdk`, then from a program whose callback looks for each event's file as the event arrives.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  CID=$(control-sdk conversation new --script S01/run_to_finish.json --field id)
  control-sdk conversation send "$CID" --text "Write result.txt" --run --check execution_status eq finished
  control-sdk conversation state "$CID" --check event_count eq 5 --check event_files eq 5 \
    --check last_agent_message eq "result.txt now says done."
  control-sdk exec --expect-output event-00000- --expect-output event-00004- -- find ../conversations -name "event-*.json"
  control-sdk exec --expect-output base_state.json -- find ../conversations -name base_state.json
  control-sdk program S04/lifecycle --arg persist \
    --check folder_is_id_hex eq true --check base_state eq true --check event_files eq 6 --check events_in_state eq 6 \
    --check names_match eq true --check numbered_in_order eq true --check files_follow_event_ids eq true \
    --check callback_saw_file_every_time eq true --check files_grew_one_by_one eq true --check status eq finished
  ```
- **Damaged event files (`S04.persist-events`).** A finished conversation is reopened after one change at a time to its `events/` folder: an empty file (what an interrupted write leaves), invalid JSON, a deleted file. Any of them makes the reload fail with an error that names the file or event; nothing is silently skipped.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/lifecycle --arg corrupt \
    --check events_before eq 6 --check intact.events eq 6 \
    --check empty_middle.error contains "Missing event file" --check empty_last.error contains "Missing event file" \
    --check invalid_json_middle.error contains "Invalid JSON" \
    --check missing_middle.error contains "Unknown event_id" --check missing_last.error contains "Unknown event_id"
  ```
- **Resume with memory (`S04.resume`).** A conversation is closed and deleted, a new `Conversation` with the same `persistence_dir` and id reloads it, and the next request to the model carries the first turn.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/lifecycle --arg resume \
    --check events_reloaded eq 3 --check status_after_load eq finished \
    --check second_request_has_first_turn eq true --check second_request_has_first_reply eq true \
    --check system_prompts_sent eq 1 --check replies.1 eq "The code word was PLUM." \
    --check status eq finished --check events_after eq 5
  ```
- **Which agent changes a resume accepts (`S04.resume-agent-compat`).** Removing a tool, removing a default tool and changing the agent class fail; adding a tool and changing the LLM work.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/lifecycle --arg compat \
    --check remove_tool contains "tools were removed mid-conversation" --check remove_tool contains file_editor \
    --check remove_default_tool contains "tools were removed mid-conversation" \
    --check change_agent_class contains "persisted agent is of type Agent" \
    --check add_tool eq finished --check change_llm eq finished
  ```
- **An injected `FileStore` (`S04.custom-file-store`).** `LocalConversation(file_store=InMemoryFileStore(), persistence_dir=...)` writes only to the store, still reports the directory, and a second conversation reloads from the store.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/lifecycle --arg file-store \
    --check store_has_base_state eq true --check store_event_files eq 6 --check nothing_on_disk eq true \
    --check reported_persistence_dir matches "lifecycle-file-store/[0-9a-f]{32}$" --check reloaded_from_store eq 6
  ```
- **Callbacks (`S04.callbacks`).** Order is visualizer, then user callbacks in list order; user callbacks run after the event is stored, the visualizer before; an event whose write fails reaches no callback.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/lifecycle --arg callbacks \
    --check order_first_event.0 eq visualizer --check order_first_event.1 eq first --check order_first_event.2 eq second \
    --check consistent_order eq true --check every_event_delivered eq true \
    --check persisted_before_user_callback eq true --check persisted_before_visualizer eq false \
    --check failed_write_error contains "disk full" --check delivered_before_failure len-eq 2 --check delivered_after_failure len-eq 0
  ```
- **An exception inside `run()` (`S04.run-error`).** The model raises a plain `RuntimeError` (persisted conversation) and an `LLMAuthenticationError` (in memory).
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/lifecycle --arg run-error \
    --check plain_exception.raised eq ConversationRunError --check plain_exception.names_id eq true \
    --check plain_exception.names_persistence_dir eq true --check plain_exception.has_issue_url eq true \
    --check plain_exception.original eq RuntimeError --check plain_exception.error_events eq 1 \
    --check plain_exception.codes.0 eq RuntimeError --check plain_exception.status eq error \
    --check llm_error.raised eq ConversationRunError --check llm_error.persistence_dir missing \
    --check llm_error.error_events eq 1 --check llm_error.codes.0 eq LLMAuthenticationError
  ```
- **Close (`S04.close`).** Each conversation's terminal is a tmux server under the run's private `TMUX_TMPDIR`; the program counts the live ones. `close()` stops it, a second `close()` is a no-op, the object can be collected afterwards, `delete_on_close=False` still stops the terminal, `__del__` closes, and persisted files stay in every case.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/lifecycle --arg close \
    --check sessions_while_open eq 1 --check sessions_after_close eq 0 --check second_close_error missing \
    --check collected_after_close eq true --check persisted_files_kept eq true \
    --check keep_sessions_while_open eq 1 --check keep_sessions_after_close eq 0 --check keep_files_kept eq true \
    --check del_sessions_while_open eq 1 --check del_sessions_after eq 0
  ```
- **Pause (`S04.pause`).** A run of four `sleeper` steps is paused from another thread after the first observation; a pause while idle; a pause while an LLM call is in flight waits for that call and its action, then stops; `run()` resumes each time.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/control --arg pause \
    --check idle_pause_status eq paused --check idle_pause_events eq 1 \
    --check status_after_pause eq paused --check pause_events eq 2 --check steps_before_stop lt 4 \
    --check status_after_resume eq finished --check steps_after_resume eq 5 --check script_left eq 0
  control-sdk program S04/control --arg pause-mid-call \
    --check status eq paused --check pause_returned_after_s gt 0.5 --check llm_calls eq 1 \
    --check actions eq 1 --check observations eq 1 --check pause_events eq 1 --check status_after_resume eq finished
  ```
- **An async program (`S04.async-callback`).** `run()` in an executor thread, events delivered to a coroutine on the main loop through `AsyncCallbackWrapper`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/control --arg async-callback \
    --check status eq finished --check run_thread_is_main eq false \
    --check received eq 6 --check events_in_state eq 6 --check callbacks_on_main_thread eq true
  ```
- **`arun()` and `interrupt()` (`S04.arun-interrupt`).** The model takes 30 s per call; `interrupt()` from another thread stops `arun()` at once without raising, then `run()` finishes the script. With no `arun()` in flight (idle, or a synchronous `run()`), `interrupt()` is a `pause()`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/control --arg interrupt \
    --check stopped_after_s lt 5 --check raised len-eq 0 --check status eq paused --check interrupt_events eq 1 \
    --check script_left_after_interrupt eq 2 --check status_after_resume eq finished --check script_left_after_resume eq 0 \
    --check idle_interrupt.status eq paused --check idle_interrupt.pause_events eq 1 --check idle_interrupt.interrupt_events eq 0 \
    --check sync_interrupt.status eq paused --check sync_interrupt.pause_events eq 1 --check sync_interrupt.interrupt_events eq 0
  ```
- **The cancellation token (`S04.cancel-token`).** One step with four 1-second `sleeper` calls, `tool_concurrency_limit=1`, interrupted during the first: the token is cancelled and the other three never start; the next run gets a new token.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/control --arg cancel-token \
    --check status eq paused --check token_cancelled eq true --check calls_started len-eq 1 --check calls_started.0 eq call-0 \
    --check interrupt_events eq 1 --check second_token_is_new eq true --check second_token_cancelled eq false \
    --check status_after_resume eq finished
  ```
- **Secrets (`S04.secrets-update`, `S04.secrets-constructor`, `S04.secrets-masking`).** `update_secrets` with a string and a `SecretSource` subclass, a key overwritten, and `Conversation(secrets=...)`; the agent echoes them in the terminal.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/secret_env \
    --check unreferenced eq "no secrets referenced here" --check static eq "token=<secret-hidden>" \
    --check from_source eq "fn=<secret-hidden>" --check length_of_value eq 22 \
    --check rotated eq "rotated=<secret-hidden>" --check length_after_rotation eq 25 \
    --check constructor eq "api=<secret-hidden> src=<secret-hidden>" \
    --check values_in_observations eq false --check values_on_disk eq false --check names_on_disk eq true \
    --check lookups_at_each_observation.0 eq 1 --check plain_callable contains "Invalid SecretValue"
  ```
  The lengths (22, then 25 after the overwrite) prove the real values reached the shell while the text the agent saw stayed masked.
- **Visualizers (`S04.visualizer-default`, `S04.visualizer-custom`).** The default prints Rich panels (with and without passing it), `None` prints nothing, an instance with `skip_user_messages=True` drops the user panel, and a `ConversationVisualizerBase` subclass receives every event.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/views --arg visualizer \
    --check default_has_user_panel eq true --check default_has_observation_panel eq true \
    --check default_shows_command eq true --check default_uses_boxes eq true --check omitted_prints_panels eq true \
    --check none_prints eq 0 --check instance_skip_user eq false \
    --check custom_saw_every_event eq true --check custom_events eq 6 --check custom_printed eq 0 \
    --check action_rendering contains "$ echo shown-in-panel" --check observation_rendering contains "shown-in-panel"
  ```
- **Fork (`S04.fork`).** A priced source run (0.28) is forked with a title, tags and another agent; the fork continues while the source stays as it was; a second fork keeps metrics and a chosen id; the fork reloads from disk.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/views --arg fork \
    --check new_id eq true --check copied_events eq 6 --check fork_status eq idle \
    --check title eq "Follow-up fork" --check tags.team eq verify --check fork_model eq openai/gpt-4.1-mini \
    --check source_cost eq 0.28 --check fork_cost_before_run eq 0 --check kept_metrics_cost eq 0.28 \
    --check chosen_id_kept eq true --check fork_events_after_run eq 9 --check source_events_after_fork_run eq 6 \
    --check fork_request_had_source_history eq true --check same_workspace eq true \
    --check fork_on_disk eq true --check reloaded_fork_events eq 9 --check reloaded_fork_title eq "Follow-up fork"
  ```
- **Branches (`S04.fork-branch`).** Each event's `parent_id` is the one before; `fork(from_event_id=first_answer)` copies three events; `navigate_to(first_answer)` then a new message starts a sibling branch; `navigate_to(None)` empties the active branch but keeps the log.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/views --arg branch \
    --check parent_chain_linear eq true --check fork_from_event_events eq 3 \
    --check fork_from_event_last_is_branch_point eq true --check fork_has_second_question eq false \
    --check new_question_parent_is_branch_point eq true --check old_branch_still_in_log eq true \
    --check old_branch_in_active eq false --check active_ends_with eq "answer on a new branch" \
    --check unknown_event_error contains "Unknown event_id" --check active_after_none eq 0 --check log_after_none eq 7
  ```
- **The goal loop (`S04.goal-loop`).** `run_goal` with a scripted judge: incomplete then complete (the follow-up names what was missing), and a capped run whose last verdict cannot be parsed.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/views --arg goal \
    --check complete.status eq complete --check complete.iterations eq 2 --check complete.score eq 0.95 \
    --check complete.followup_names_missing eq true --check complete.user_messages eq 2 \
    --check capped.status eq capped --check capped.iterations eq 2 \
    --check capped.last_score eq 0 --check capped.last_missing contains "could not be parsed"
  ```
- **Interactive terminal (`S04.interactive-terminal`).** With `no_change_timeout_seconds=3`, `python3` waits for input after 3 s and the next commands are typed into the REPL; a callback collects every LLM message, tool results included. A program waiting on `input()` is then answered the way the terminal tool tells the model to: `is_input=True`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/views --arg interactive \
    --check repl.status eq finished --check repl.waiting eq true --check repl.reply contains 42 \
    --check repl.collected_tool_messages eq 5 --check repl.collected_roles contains tool \
    --check prompt_is_input.waiting eq true --check prompt_is_input.reply contains "hello John 42"
  ```
  Known failure (reproduced 2026-10-13, not yet filed): with the default tmux pane pool, every terminal call first interrupts a command that is still running (`_prepare_pooled_session` sends Ctrl+C when the previous status was a no-change timeout), so a program waiting on `input()` cannot be answered. `is_input=True` returns `No previous running command to interact with.` and a plain `John` reaches bash after the program was killed (`bash: John: command not found`, `prompt_plain.reply`). The Python REPL only survives because it ignores Ctrl+C.
- **Final response, title and tags (`S04.final-response`, `S04.title-and-tags`).** `get_agent_final_response` on finished conversations; `generate_title` from the agent's LLM, an explicit LLM, a reply with `<think>` reasoning, a failing LLM and no user message; tags kept on disk, bad keys and long values refused.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/views --arg final \
    --check finish eq "the finish message" --check text_reply eq "a plain text answer" --check nothing len-eq 0 \
    --check finish_then_text eq "then talked" --check text_then_finish eq "then finished"
  control-sdk program S04/views --arg title-tags \
    --check agent_llm eq "Agent model title" --check reasoning_stripped eq "Fix login reset emails" \
    --check explicit_llm eq "Title from the explicit LLM" \
    --check fallback_on_error eq "Please fix the login bug that breaks password r..." \
    --check fallback_max_length eq "Please fix the lo..." --check only_reasoning matches "^Please fix" \
    --check no_user_message contains "No user messages found" \
    --check tags.env eq verify --check tags_on_disk.team2 eq sdk \
    --check bad_tags.0 contains "lowercase alphanumeric" --check bad_tags.1 contains "lowercase alphanumeric" \
    --check bad_tags.2 contains "maximum length of 256"
  ```
- **Conversation stats (`S04.stats`).** A priced conversation (two calls at 0.14) plus a second LLM in the conversation's registry, then a resume from disk. The snapshot form of stats update events is not reachable here: a local run never emits them.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S02/conversation_stats --arg after-run \
    --check first_run.usage_ids contains agent --check first_run.usage_ids contains secondary \
    --check first_run.agent_cost eq 0.28 --check first_run.secondary_cost eq 0.14 --check first_run.combined_cost eq 0.42 \
    --check resumed.restored_agent_cost eq 0.28 --check resumed.agent_cost_after eq 0.42 --check resumed.status eq finished
  ```
- **A real model across a resume (`S04.factory`, `S04.resume`, `S04.final-response`).** Live; each `control-sdk` command reopens the conversation from disk.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  CID=$(control-sdk conversation new --field id)
  control-sdk conversation send "$CID" --run --text "Remember the code word PLUM. Reply with OK only." \
    --check execution_status eq finished
  control-sdk conversation send "$CID" --run --text "What was the code word? Reply with the word only." \
    --check execution_status eq finished --check last_agent_message contains PLUM
  ```
- **Examples 09, 10, 11 and 12 (`S04.pause`, `S04.persist-events`, `S04.resume`, `S04.async-callback`, `S04.secrets-update`, `S04.secrets-masking`, `S04.visualizer-default`).** Live; example 10 also needs `uvx` and network.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output "Final status" --expect-output EXAMPLE_COST -- python examples/01_standalone_sdk/09_pause_example.py
  control-sdk exec --model-env --expect-output "Deserializing conversation" --expect-output EXAMPLE_COST -- python examples/01_standalone_sdk/10_persistence.py
  control-sdk exec --model-env --expect-output EXAMPLE_COST -- python examples/01_standalone_sdk/11_async.py
  control-sdk exec --model-env --expect-output "<secret-hidden>" --expect-output EXAMPLE_COST -- python examples/01_standalone_sdk/12_custom_secrets.py
  ```
- **Examples 26, 48, 50 and 54 (`S04.visualizer-custom`, `S04.fork`, `S04.fork-branch`, `S04.title-and-tags`, `S04.arun-interrupt`, `S04.cancel-token`, `S04.goal-loop`).** Live.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output "[EVENT] ActionEvent" --expect-output EXAMPLE_COST -- python examples/01_standalone_sdk/26_custom_visualizer.py
  control-sdk exec --model-env --expect-output "fork() works end-to-end" -- python examples/01_standalone_sdk/48_conversation_fork.py
  control-sdk exec --model-env --env AUTO_CANCEL_SECONDS=5 --expect-output EXAMPLE_COST -- python examples/01_standalone_sdk/50_async_cancellation.py
  control-sdk exec --model-env --expect-output "Judge score" -- python examples/01_standalone_sdk/54_goal_completion_loop.py
  ```
- **Example 06 and integration test t07 (`S04.interactive-terminal`).** Live.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output "Got the following LLM messages" -- python examples/01_standalone_sdk/06_interactive_terminal_w_reasoning.py
  control-sdk exec --model-env -- python tests/integration/run_infer.py --llm-config '{"model": "deepseek/deepseek-flash"}' --eval-ids t07_interactive_commands
  ```
- **`Conversation(...)` with a `RemoteWorkspace` returns a `RemoteConversation` (`S04.factory`), blocked.** Needs a running agent server (family S05); without one only the `persistence_dir` refusal above is reachable.

## Gotchas

- `examples/01_standalone_sdk/06_interactive_terminal_w_reasoning.py` is in `_EXCLUDED_EXAMPLES` in `tests/examples/test_examples.py`, so the `test-examples` label never runs it; it also prints no `EXAMPLE_COST:` line, which that harness would require.
- Examples run under the harness with `uv run pytest tests/examples/test_examples.py --run-examples -k <name>`; without `--run-examples` every case is skipped.
- `10_persistence.py` and `14_context_condenser.py` write `./.conversations` relative to the current directory; reruns pile up conversation folders there. Example 10 starts `uvx ... mcp-server-fetch` and fetches github.com, so it needs `uvx` and network.
- `pause()` does not interrupt an LLM call in flight; it takes effect at the next step. Use `arun()` + `interrupt()` for immediate cancellation. `interrupt()` during a synchronous `run()` is only a `pause()`.
- `09_pause_example.py` sleeps a fixed 2 s before pausing; on a slow model the first step may not have started, which still passes but shows less.
- `50_async_cancellation.py` installs a SIGINT handler and defaults `LLM_MODEL` to an Anthropic model, unlike the other examples.
- The default visualizer prints Rich panels to stdout; pass `visualizer=None` when you parse stdout.
- `OH_PERSISTENCE_DIR` relocates `~/.openhands` (user state, not `persistence_dir`); it must be set before `openhands.sdk` is imported.
- The terminal-session tests need `tmux`; they are slow (about 40 s for the two interactive cases).
- No failing tests were found in this family's commands at 4cc0582.
- The visualizer runs before the event is stored and user callbacks after it, in list order; a callback that looks the event up in `conversation.state.events` finds it, a visualizer does not.
- `pause()` takes the state lock, so when an LLM call is in flight it blocks the calling thread until that call (and its tool call) finishes.
- A damaged event file is not skipped on reload: an empty file gives `FileNotFoundError: Missing event file`, invalid JSON a `ValidationError`, and a deleted file `KeyError: 'Unknown event_id: ...'`, so the conversation cannot be reopened.
- `update_secrets`/`secrets=` accept a `str` or a `SecretSource` subclass only; a plain `lambda` raises `ValueError: Invalid SecretValue` although the `update_secrets` docstring speaks of callables. Every `SecretSource` is resolved the first time any command output is masked, not only when a command names it.
- With tmux available the terminal runs in a pane pool and each call first sends Ctrl+C to a command left running by a no-change timeout, so a program waiting on `input()` cannot be answered (`is_input=True` gets `No previous running command to interact with.`); the Python REPL survives only because it ignores Ctrl+C.
- Each terminal starts its own tmux server with a socket under `$TMUX_TMPDIR/tmux-<uid>/`; the socket file stays after `close()` stops the server, so count live servers (`tmux -S <socket> ls`), not files.
