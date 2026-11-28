# Local conversation lifecycle

A Python program calls `Conversation(agent=..., workspace=...)` with a local workspace (a path string or `LocalWorkspace`) and gets a `LocalConversation` that runs the agent in the same process. Around the step loop (family S01) it owns the conversation's lifecycle: a stable `conversation.id`, optional on-disk persistence under `persistence_dir/<id.hex>/` (`base_state.json` plus one `events/event-NNNNN-<event_id>.json` per event) that a second `Conversation(..., persistence_dir=..., conversation_id=same_id)` reloads and continues, `pause()` from another thread, `arun()` on an asyncio loop with `interrupt()` cancelling mid-LLM-call, `update_secrets(...)` that inject values as environment variables into terminal commands and mask them in output, a pluggable visualizer (Rich console output by default, `visualizer=None` for silence), `fork()` into an independent copy or a branch from a chosen event, and `close()`. Helpers next to it read results out of a finished conversation: `get_agent_final_response(events)`, `conversation.conversation_stats` (per-`usage_id` LLM metrics), `generate_title()`, and `run_goal(...)`, which re-prompts the agent until a second judge LLM agrees an objective is complete, and stops with `GoalAsked` when the agent's `ask_user` question awaits the user.

Source: `openhands-sdk/openhands/sdk/conversation/conversation.py`, `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`, `openhands-sdk/openhands/sdk/conversation/base.py`, `openhands-sdk/openhands/sdk/conversation/state.py`, `openhands-sdk/openhands/sdk/conversation/persistence_const.py`, `openhands-sdk/openhands/sdk/conversation/cancellation.py`, `openhands-sdk/openhands/sdk/conversation/secret_registry.py`, `openhands-sdk/openhands/sdk/conversation/visualizer/`, `openhands-sdk/openhands/sdk/conversation/goal/`, `openhands-sdk/openhands/sdk/conversation/resolution.py`, `openhands-sdk/openhands/sdk/conversation/response_utils.py`, `openhands-sdk/openhands/sdk/conversation/conversation_stats.py`, `openhands-sdk/openhands/sdk/conversation/title_utils.py`, `openhands-sdk/openhands/sdk/conversation/types.py`, `openhands-sdk/openhands/sdk/secret/`, `openhands-sdk/openhands/sdk/utils/async_utils.py`

Needs: `llm`, `tmux`, `network`, `uvx`

Examples: `examples/01_standalone_sdk/06_interactive_terminal_w_reasoning.py`, `examples/01_standalone_sdk/09_pause_example.py`, `examples/01_standalone_sdk/10_persistence.py`, `examples/01_standalone_sdk/11_async.py`, `examples/01_standalone_sdk/12_custom_secrets.py`, `examples/01_standalone_sdk/26_custom_visualizer.py`, `examples/01_standalone_sdk/48_conversation_fork.py`, `examples/01_standalone_sdk/50_async_cancellation.py`, `examples/01_standalone_sdk/54_goal_completion_loop.py`

## Sub-features

- `S04.factory`: `Conversation(...)` returns a `LocalConversation` for a path or `LocalWorkspace` and a `RemoteConversation` for a `RemoteWorkspace`; `persistence_dir` is only accepted on the local branch. Unit: `tests/sdk/conversation/test_conversation_factory.py`. Example: `examples/01_standalone_sdk/10_persistence.py`. Real-LLM: every `tests/integration/tests/t*.py` builds a `LocalConversation` through `tests/integration/base.py`.
- `S04.conversation-id`: each conversation gets a unique UUID, a passed `conversation_id` is kept and persisted, and the id pins the LLM prompt-cache key. Unit: `tests/sdk/conversation/local/test_conversation_id.py`. Example: `examples/01_standalone_sdk/10_persistence.py`. Real-LLM: none.
- `S04.persist-events`: with `persistence_dir`, every event is written as `events/event-NNNNN-<id>.json` and base state as `base_state.json` under `<persistence_dir>/<id.hex>/`, incrementally; on load, an empty, invalid or missing event file makes the reload fail with an error naming the file or event. Unit: `tests/sdk/conversation/local/test_state_serialization.py`, `tests/sdk/conversation/local/test_conversation_core.py`, `tests/cross/test_persistence_dir_call_sites.py`. Example: `examples/01_standalone_sdk/10_persistence.py`. Real-LLM: none.
- `S04.resume`: a new `Conversation` with the same `persistence_dir` and `conversation_id` reloads the events and state and continues the dialogue with full memory. Unit: `tests/sdk/conversation/local/test_state_serialization.py::test_conversation_state_persistence_save_load`, `tests/cross/test_conversation_restore_behavior.py::test_conversation_restore_lifecycle_happy_path`. Example: `examples/01_standalone_sdk/10_persistence.py`. Real-LLM: none.
- `S04.ask-agent-usage-saved`: with a `persistence_dir`, the usage of a `conversation.ask_agent(...)` call (`conversation_stats.usage_to_metrics["ask-agent-llm"]`) is written to `base_state.json` when the call returns, so a conversation reloaded from that directory counts it. Unit: **none (untested)**. Example: none. Real-LLM: none.
- `S04.resume-agent-compat`: on resume, removing tools, removing default tools or changing the agent class fails; adding tools and changing the LLM, condenser or skills succeeds; security risk and summaries survive. Unit: `tests/cross/test_conversation_restore_behavior.py`. Example: none. Real-LLM: none.
- `S04.custom-file-store`: an injected `FileStore` takes precedence over `persistence_dir` for where state is written, while `persistence_dir` is still reported. Unit: `tests/sdk/conversation/local/test_custom_file_store.py`. Example: none. Real-LLM: none.
- `S04.callbacks`: `callbacks=[...]` receive every event after the default callback has persisted it; a persistence failure blocks downstream callbacks; the visualizer runs in a fixed order relative to user callbacks. Unit: `tests/sdk/conversation/local/test_conversation_default_callback.py`, `tests/sdk/conversation/local/test_conversation_visualize_param.py::test_conversation_callback_order`. Example: `examples/01_standalone_sdk/10_persistence.py`. Real-LLM: none.
- `S04.run-error`: an exception inside `run()` surfaces as `ConversationRunError` that names the conversation id, the persistence dir and the issue URL, and emits one `ConversationErrorEvent` (no duplicate when the agent already emitted one). Unit: `tests/sdk/conversation/local/test_run_exception_includes_conversation_id.py`. Example: none. Real-LLM: none.
- `S04.close`: `close()` closes tool executors (terminal sessions), is idempotent, unregisters its atexit hook, and still closes the executors with `delete_on_close=False` (persisted files stay either way); `__del__` also closes. Unit: `tests/sdk/conversation/test_atexit_cleanup.py`, `tests/tools/terminal/test_conversation_cleanup.py`, `tests/sdk/conversation/local/test_conversation_core.py::test_close_propagates_agent_failure_and_allows_retry`. Example: none. Real-LLM: none.
- `S04.pause`: `pause()` from another thread moves RUNNING or IDLE to PAUSED, emits exactly one `PauseEvent`, takes effect between steps (not mid-LLM-call), and a later `run()` resumes. Unit: `tests/sdk/conversation/local/test_conversation_pause_functionality.py`, `tests/sdk/conversation/local/test_agent_status_transition.py::test_execution_status_transitions_to_running_from_paused`. Example: `examples/01_standalone_sdk/09_pause_example.py`. Real-LLM: `tests/integration/tests/c04_token_condenser.py` and `c05_size_condenser.py` call `pause()`.
- `S04.pause-during-startup`: a `pause()` from another thread that arrives after `run()` was called but while `run()` is still making the agent ready (plugins, MCP servers; the first `run()` of a conversation restored from `persistence_dir`) keeps the status PAUSED, makes no LLM call, and the next `run()` resumes; a conversation already PAUSED when `run()` is called is resumed by it. Unit: `tests/sdk/conversation/local/test_conversation_pause_functionality.py` (a pause before `run()` only; the startup window is **untested**). Example: none. Real-LLM: none.
- `S04.async-callback`: `AsyncCallbackWrapper(coro, loop)` lets a synchronous `run()` in an executor thread deliver events to a coroutine on the main loop. Unit: `tests/sdk/utils/test_async_utils.py`. Example: `examples/01_standalone_sdk/11_async.py`. Real-LLM: none.
- `S04.arun-interrupt`: `await conversation.arun()` uses the async LLM path; `interrupt()` (safe from a signal handler or another thread) cancels it at once, even mid-LLM-call, sets PAUSED, emits an `InterruptEvent`, does not raise `CancelledError`, and the conversation can be resumed; with no `arun()` in flight it falls back to `pause()`. Unit: `tests/sdk/conversation/test_interrupt.py`. Example: `examples/01_standalone_sdk/50_async_cancellation.py`. Real-LLM: none.
- `S04.arun-pause-persisted`: with a `persistence_dir`, a `pause()` made from another thread while `arun()` waits on the model returns at once and is in `base_state.json` (`execution_status: paused`) at once, before the model call returns; `arun()` then stops PAUSED after that call and a later `run()` finishes. Unit: `tests/sdk/agent/test_astep_releases_state_lock.py::test_pause_during_llm_call_is_persisted_at_once`. Example: none. Real-LLM: none.
- `S04.cancel-token`: each `run()`/`arun()` gets a fresh `CancellationToken` that `interrupt()` sets before cancelling, so pending parallel tool calls are skipped. Unit: `tests/sdk/conversation/test_interrupt.py::test_interrupt_sets_cancel_token`, `::test_execute_batch_skips_all_on_pre_cancelled_token`. Example: `examples/01_standalone_sdk/50_async_cancellation.py`. Real-LLM: none.
- `S04.secrets-update`: `update_secrets({...})` accepts plain strings and `SecretSource` objects (resolved on first use, which is the first command output the conversation masks, then cached), overwrites existing keys, and exports referenced keys as environment variables for terminal commands. Unit: `tests/sdk/conversation/test_secrets_manager.py`, `tests/sdk/conversation/test_secret_source.py`. Example: `examples/01_standalone_sdk/12_custom_secrets.py`. Real-LLM: none.
- `S04.secrets-constructor`: `Conversation(secrets={...})` seeds the same registry at construction, for static and callable values. Unit: `tests/sdk/conversation/test_conversation_secrets_constructor.py`. Example: none. Real-LLM: none.
- `S04.secrets-masking`: secret values echoed by a command are replaced by `<secret-hidden>` in observations and never reach the persisted events. Unit: `tests/tools/terminal/test_secrets_masking.py`, `tests/cross/test_agent_secrets_integration.py`. Example: `examples/01_standalone_sdk/12_custom_secrets.py`. Real-LLM: none.
- `S04.visualizer-default`: by default a `DefaultConversationVisualizer` prints each event as a Rich panel; `visualizer=None` turns it off; a class or instance can be passed. Unit: `tests/sdk/conversation/local/test_conversation_visualize_param.py`. Example: `examples/01_standalone_sdk/10_persistence.py`. Real-LLM: none.
- `S04.visualizer-custom`: a `ConversationVisualizerBase` subclass passed as `visualizer=` receives every event through `on_event`; each event type has its own `visualize` rendering. Unit: `tests/sdk/conversation/test_visualizer.py`. Example: `examples/01_standalone_sdk/26_custom_visualizer.py`. Real-LLM: none.
- `S04.fork`: `fork()` deep-copies the conversation under a new id (or `conversation_id=`), starts IDLE, leaves the source untouched, resets metrics unless `reset_metrics=False`, accepts `agent=` and `tags=` (`title=` is deprecated and ignored locally; only `RemoteConversation` stores it), shares the workspace, and persists so the fork survives a reload. Unit: `tests/sdk/conversation/local/test_fork.py`. Example: `examples/01_standalone_sdk/48_conversation_fork.py`. Real-LLM: none.
- `S04.fork-keeps-secrets`: `fork()` of a `LocalConversation(..., cipher=...)` with a `persistence_dir` creates the fork with the source's cipher and copies the source's conversation secrets (`secrets=`, `update_secrets`), so the fork's `base_state.json` holds the LLM api key and every secret encrypted (not redacted); `LocalConversation(agent=None, conversation_id=fork_id, cipher=...)` reopens the fork with the real key and secrets, and it runs. Without a cipher the fork's key stays redacted on disk. Since [#5765](https://github.com/OpenHands/software-agent-sdk/pull/5765) ([#5465](https://github.com/OpenHands/software-agent-sdk/issues/5465)). Unit: `tests/sdk/conversation/local/test_fork.py::test_fork_keeps_source_cipher_for_persisted_secrets`, `::test_fork_without_cipher_still_redacts_secrets`, `tests/agent_server/test_conversation_service.py::TestConversationTreeForkAndNavigate::test_cipher_backed_fork_resumes_with_credentials`. Example: none. Real-LLM: none.
- `S04.fork-branch`: events carry parent ids forming a tree; `fork(from_event_id=...)` copies only that branch, and `navigate_to(event_id)` moves HEAD so the next event starts a sibling branch (`None` empties the active branch). Unit: `tests/sdk/conversation/local/test_conversation_tree.py`. Example: `examples/01_standalone_sdk/48_conversation_fork.py`. Real-LLM: none.
- `S04.goal-loop`: `run_goal(conversation, objective, judge_llm, max_iterations=N)` sends the objective, runs, asks the judge LLM for a verdict, re-prompts with the missing items, and returns a `GoalOutcome` with status `"complete"` or `"capped"`, the iteration count and the verdict. Unit: `tests/sdk/conversation/goal/`. Example: `examples/01_standalone_sdk/54_goal_completion_loop.py`. Real-LLM: none.
- `S04.goal-asked`: when a round ends on the agent's `ask_user` question, `run_goal` returns `GoalAsked(ask_id, question)` for it without calling the judge, and the objective it sent is a user message with `sender="goal"`, which does not answer the question; called again while the question is pending it returns the same `GoalAsked` at once, sending nothing and calling neither model; once the user has answered (`conversation.answer_ask(...)` plus the returned message) it runs and the judge can say complete. Unit: `tests/tools/ask_user/test_ask_user.py::test_a_goal_stops_on_a_question_without_judging`. Example: none. Real-LLM: none.
- `S04.goal-asked-branch`: only a question on the active branch stops a goal: after `navigate_to(...)` back to the user message before an unanswered question, `run_goal` with a new objective sends it (`sender="goal"`), runs the agent and asks the judge. Unit: **none (untested)**. Example: none. Real-LLM: none.
- `S04.interactive-terminal`: with `Tool(name=TerminalTool.name, params={"no_change_timeout_seconds": 3})` the agent can start `python3`, feed it input and exit; an `LLMConvertibleEvent` callback collects the raw LLM messages. Unit: `tests/tools/terminal/test_terminal_session.py::test_python_interactive_input`, `::test_interactive_command`. Example: `examples/01_standalone_sdk/06_interactive_terminal_w_reasoning.py`. Real-LLM: `tests/integration/tests/t07_interactive_commands.py`.
- `S04.final-response`: `get_agent_final_response(conversation.state.events)` returns the text of whichever came last of the agent's `finish` call and its plain-text `MessageEvent`, else `""`. Unit: `tests/sdk/conversation/test_agent_final_response.py`. Example: none. Real-LLM: `tests/integration/tests/t08_image_file_viewing.py`.
- `S04.stats`: `conversation.conversation_stats` keeps `Metrics` per LLM `usage_id`, combines them with `get_combined_metrics()`, restores them on resume, and stats update events carry a snapshot rather than the full history. Unit: `tests/sdk/conversation/test_conversation_stats.py`, `tests/sdk/conversation/test_stats_update_event_snapshot.py`. Example: `examples/01_standalone_sdk/11_async.py` (prints cost). Real-LLM: none.
- `S04.title-and-tags`: `generate_title(llm=None, max_length=50)` asks an LLM (the agent's by default) for a title from the first user message, falls back to truncation on error, and strips reasoning text; `tags={...}` keys must be lowercase alphanumeric. Unit: `tests/sdk/conversation/test_generate_title.py`, `tests/sdk/conversation/test_tags.py`. Example: `examples/01_standalone_sdk/48_conversation_fork.py` (`title=`). Real-LLM: none.
- `S04.conversation-sender`: `conversation_sender(conversation_id)` (`openhands.sdk.conversation.types`) is `CONVERSATION_SENDER_PREFIX` (`"conversation:"`) plus the id's hex; `send_message(text, sender=...)` records it on the saved user `MessageEvent`, a plain `send_message` records no sender, and both survive a reload from `persistence_dir`. Unit: **none (untested)** in the SDK; `tests/agent_server/test_event_router.py` covers the server's use of it. Example: none. Real-LLM: none.
- `S04.resolution-verdict`: `verdict_from_judgments(judgments)` (`openhands.sdk.conversation.resolution`) turns the seven yes/no judgments of `RESOLUTION_QUESTIONS` into a `ResolutionVerdict`: `owed` `nothing` (resolved) only when `handled` and `task_done` are both at least 0.85 and every hand-off stays below 0.2; otherwise the strongest hand-off at or above 0.5 (`answer`, `review`, `decide`, `check`), else `unfinished` when the work is not done, else `unclear`; `probability` is `min(handled, task_done, 1 - strongest hand-off)`, `needs_user` is true only for a question, review, choice or check (not a reported problem), and judgments are clamped to 0..1, a non-numeric one counting as 0. Unit: `tests/sdk/conversation/test_resolution.py`. Example: none. Real-LLM: none.
- `S04.resolution-judge`: `resolution_judge(fallback_llm, LLMProfileStore(...), cipher=None)` loads the saved LLM profile `resolution_judge`, first saving a copy of the fallback LLM under that name (with its key, encrypted on disk when a cipher is given) when the store has none; an existing profile wins over a later fallback. The returned `LLMDecisionModel` runs on that profile with a 60 s timeout and no LLM-level retries (`usage_id` `resolution_judge`), and `await judge_resolution(judge, ResolutionState(...))` sends the turn and every question in one chat request and returns the verdict with the profile's model; a reply that is not JSON raises `ValueError` after one request. Unit: `tests/sdk/conversation/test_resolution.py`. Example: none. Real-LLM: none.

## How to get to it (agent POV)

- `Conversation(agent, workspace=..., persistence_dir=None, conversation_id=None, callbacks=None, visualizer=DefaultConversationVisualizer, secrets=None, delete_on_close=True, tags=None, ...)` in `openhands.sdk`: `S04.factory`, `S04.conversation-id`, `S04.persist-events`, `S04.resume`, `S04.resume-agent-compat`, `S04.callbacks`, `S04.visualizer-*`, `S04.secrets-constructor`, `S04.title-and-tags`. The step-loop arguments (`max_iteration_per_run`, `stuck_detection*`, `max_budget_per_run`) belong to S01.
- On disk: `<persistence_dir>/<conversation_id.hex>/base_state.json` and `.../events/event-NNNNN-<event_id>.json` (`persistence_const.py`): `S04.persist-events`, `S04.resume`, `S04.ask-agent-usage-saved` (`stats.usage_to_metrics` in `base_state.json`). `ConversationState.create(..., file_store=...)` / `LocalConversation(..., file_store=...)`: `S04.custom-file-store`.
- `conversation.run()`, `conversation.pause()`, `await conversation.arun()`, `conversation.interrupt()`, `conversation.cancel_token`: `S04.pause`, `S04.pause-during-startup`, `S04.arun-interrupt`, `S04.arun-pause-persisted`, `S04.cancel-token`. `openhands.sdk.utils.async_utils.AsyncCallbackWrapper`: `S04.async-callback`.
- `conversation.update_secrets({...})`, `openhands.sdk.secret.SecretSource` (subclass with `get_value()`), `StaticSecret`, `LookupSecret`: `S04.secrets-*`.
- `openhands.sdk.conversation.visualizer.ConversationVisualizerBase`, `DefaultConversationVisualizer`: `S04.visualizer-*`.
- `conversation.fork(conversation_id=, agent=, title=, tags=, reset_metrics=True, from_event_id=)`, `conversation.navigate_to(event_id)`: `S04.fork`, `S04.fork-branch`; `LocalConversation(..., cipher=Cipher(key))` (`openhands.sdk.utils.cipher`; the `Conversation` factory does not take a cipher): `S04.fork-keeps-secrets`.
- `openhands.sdk.conversation.goal.run_goal`, `GoalController`, `judge_goal`: `S04.goal-loop`; `GoalAsked` (a round that ended on an `ask_user` question, `openhands.tools.ask_user`, see S16): `S04.goal-asked`, `S04.goal-asked-branch`.
- `openhands.sdk.conversation.types.conversation_sender`, `CONVERSATION_SENDER_PREFIX`, `conversation.send_message(..., sender=...)`: `S04.conversation-sender`. `openhands.sdk.conversation.resolution` (`verdict_from_judgments`, `resolution_judge`, `judge_resolution`, `ResolutionState`, `ResolutionVerdict`, `LLMDecisionModel`, `RESOLUTION_JUDGE_PROFILE_NAME`): `S04.resolution-verdict`, `S04.resolution-judge`; the profile lives in an `LLMProfileStore` directory (see S03).
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
- **Damaged event files (`S04.persist-events`).** A finished conversation is reopened after one change at a time to its `events/` folder: an empty file (what an interrupted write leaves), invalid JSON, a deleted file. The conversation reopens every time without an error: a deleted file is skipped and the other events reload; an empty or invalid file is read as a `ConversationErrorEvent` stand-in (code `UnreadableEventFile`) in the same place, so the event count is unchanged (`S05.event-log-recovery`).
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/lifecycle --arg corrupt \
    --check events_before eq 6 --check intact.events eq 6 \
    --check empty_middle.error missing --check empty_middle.events eq 6 --check empty_last.error missing --check empty_last.events eq 6 \
    --check invalid_json_middle.error missing --check invalid_json_middle.events eq 6 \
    --check missing_middle.error missing --check missing_middle.events eq 5 \
    --check missing_last.error missing --check missing_last.events eq 5
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
- **`ask_agent` usage on disk and after a reload (`S04.ask-agent-usage-saved`).** A persisted conversation on a priced model (0.14 per call) finishes one turn, then answers one `ask_agent`. The program reads the stats in memory and in `base_state.json` right after the ask, then opens a new `Conversation` on a copy of the persistence directory taken at that moment.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/lifecycle --arg ask-usage \
    --check status eq finished --check answer eq "A side answer." --check requests eq 2 \
    --check disk_after_turn.agent.cost eq 0.14 \
    --check memory_after_ask.agent.cost eq 0.14 --check memory_after_ask.ask-agent-llm.cost eq 0.14 \
    --check disk_after_ask.agent.cost eq 0.14 --check disk_after_ask.ask-agent-llm.cost eq 0.14 \
    --check reloaded.ask-agent-llm.cost eq 0.14 --check reloaded_combined_cost eq 0.28
  ```
  Expected: the ask's 0.14 is under `ask-agent-llm` in memory, in `base_state.json` and in the reloaded conversation, whose combined cost is 0.28.
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
- **A pause while `run()` is starting (`S04.pause-during-startup`).** A conversation restored from `persistence_dir` whose agent has a stdio MCP server (`S09/mcp_server.py --slow-start 2`) runs in a thread; `pause()` follows 0.5 s later, while `run()` is still starting the server. The status read under the state lock right after `pause()`, the status when `run()` returns, the LLM calls made after the pause, then a second `run()`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/pause_during_startup \
    --check status_after_restore eq idle --check status_after_pause eq paused --check pause_events eq 1 \
    --check status_after_run eq paused --check llm_calls_after_pause eq 0 \
    --check status_after_resume eq finished --check llm_calls_total eq 1 --check script_left eq 0
  ```
  Expected: `run()` returns PAUSED without calling the model, and the second `run()` makes the one call and finishes. Known failure (reproduced 2026-11-17, [#6848](https://github.com/OpenHands/software-agent-sdk/issues/6848), fix in [#6849](https://github.com/OpenHands/software-agent-sdk/pull/6849)): `run()` makes the agent ready (`_ensure_agent_ready()`) while the status is still IDLE, so `pause()` moves IDLE to PAUSED (it waits for the server, about 3.5 s, then returns PAUSED with one `PauseEvent`), and `run()` then treats PAUSED as a resume: it moves to RUNNING, calls the model once after the pause and ends FINISHED (`status_after_run` finished, `llm_calls_after_pause` 1); the second `run()` has nothing left to do.
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
- **A pause during `arun()` is on disk at once (`S04.arun-pause-persisted`).** A persisted conversation whose model takes 3 s per call runs under `arun()`; `pause()` from another thread while the first call is in flight, with `base_state.json` read before the pause, right after it and after `arun()` returns; then `run()` resumes.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/control --arg arun-pause-persisted \
    --check disk_status_before_pause eq running --check pause_returned_after_s lt 1 \
    --check disk_status_mid_call eq paused --check model_call_still_in_flight eq true \
    --check status eq paused --check disk_status_after_arun eq paused \
    --check llm_calls eq 1 --check pause_events eq 1 \
    --check status_after_resume eq finished --check disk_status_after_resume eq finished
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
- **Fork (`S04.fork`).** A priced source run (0.28) is forked with tags, another agent and a title, which is accepted and ignored; the fork continues while the source stays as it was; a second fork keeps metrics and a chosen id; the fork reloads from disk.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/views --arg fork \
    --check new_id eq true --check copied_events eq 6 --check fork_status eq idle \
    --check title missing --check tags.team eq verify --check fork_model eq openai/gpt-4.1-mini \
    --check source_cost eq 0.28 --check fork_cost_before_run eq 0 --check kept_metrics_cost eq 0.28 \
    --check chosen_id_kept eq true --check fork_events_after_run eq 9 --check source_events_after_fork_run eq 6 \
    --check fork_request_had_source_history eq true --check same_workspace eq true \
    --check fork_on_disk eq true --check reloaded_fork_events eq 9 --check reloaded_fork_title missing
  ```
- **A cipher-backed fork keeps its secrets (`S04.fork-keeps-secrets`).** `LocalConversation(cipher=Cipher(...), persistence_dir=..., secrets={"VERIFY_TOKEN": ...})` on a priced `LLM` with an api key (pointed at `S02/fake_provider.py`), plus `update_secrets({"LATER_TOKEN": ...})`, then `fork()`. The program classifies each field of the fork's `base_state.json` as `encrypted` (decrypts with the cipher to the value), `plaintext` or something else; closes both, reopens the fork with `LocalConversation(agent=None, conversation_id=fork_id, cipher=...)` and runs it: the model's terminal command echoes both secrets and prints their lengths (12 and 16). A cipher-less fork is the control.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/views --arg fork-secrets \
    --check fork_on_disk.api_key eq encrypted --check fork_on_disk.VERIFY_TOKEN eq encrypted \
    --check fork_on_disk.LATER_TOKEN eq encrypted --check fork_registry_has len-eq 2 \
    --check reloaded_api_key_is_real eq true --check reloaded_registry.VERIFY_TOKEN eq true \
    --check reloaded_registry.LATER_TOKEN eq true --check reloaded_status eq finished \
    --check requests eq 2 --check authorization.0 eq true --check authorization.1 eq true \
    --check command_output.0 eq "v=<secret-hidden> l=<secret-hidden>" \
    --check command_output.1 eq 12 --check command_output.2 eq 16 --check values_in_output eq false \
    --check fork_on_disk_after_run.api_key eq encrypted --check no_cipher_fork_api_key eq redacted
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
- **A goal stops on the agent's question (`S04.goal-asked`).** A scripted agent with only `ask_user` asks during the first round; the judge is a separate `TestLLM`, so the program counts each model's calls. `run_goal` is called again while the question is pending, then once more after `conversation.answer_ask(ask_id, 1)` and its message.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/goal_ask --arg asked \
    --check first.kind eq GoalAsked --check first.question eq "Delete the old build caches?" \
    --check first.ask_id_is_pending eq true --check first.agent_calls eq 1 --check first.judge_calls eq 0 \
    --check first.user_messages len-eq 1 --check first.user_messages.0.sender eq goal \
    --check again.kind eq GoalAsked --check again.same_question eq true \
    --check again.agent_calls eq 1 --check again.judge_calls eq 0 --check again.user_messages eq 1 \
    --check answered.kind eq GoalOutcome --check answered.status eq complete \
    --check answered.agent_calls eq 2 --check answered.judge_calls eq 1 --check answered.pending eq false
  ```
  The first round asks and the loop returns the question unjudged; the second call returns it again without a new message or model call; after the answer the loop runs one round and the judge says complete.
- **A question on an abandoned branch (`S04.goal-asked-branch`).** The agent asks, the program navigates back to the user message before the question (no question is pending on the active branch), and calls `run_goal` with a new objective.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/goal_ask --arg branch \
    --check asked_before_navigation eq true --check pending_on_active_branch eq false \
    --check outcome.kind eq GoalOutcome --check outcome.status eq complete \
    --check outcome.objective_sent eq true --check outcome.agent_calls eq 2 --check outcome.judge_calls eq 1
  ```
  Expected: the objective is sent with `sender="goal"`, the agent runs (its second call) and the judge says complete. Known failure (reproduced 2026-11-17, [#6907](https://github.com/OpenHands/software-agent-sdk/issues/6907)): `run_goal` returns `GoalAsked` for the abandoned question at once (`outcome.kind` is `GoalAsked`, `objective_sent` false, `agent_calls` 1, `judge_calls` 0): its pending-question check, and the controller's check after each round, read `conversation.state.events`, the whole log with abandoned branches, instead of the active branch.
- **Interactive terminal (`S04.interactive-terminal`).** With `no_change_timeout_seconds=3`, `python3` waits for input after 3 s and the next commands are typed into the REPL; a callback collects every LLM message, tool results included. A program waiting on `input()` is then answered the way the terminal tool tells the model to: `is_input=True`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/views --arg interactive \
    --check repl.status eq finished --check repl.waiting eq true --check repl.reply contains 42 \
    --check repl.collected_tool_messages eq 5 --check repl.collected_roles contains tool \
    --check prompt_is_input.waiting eq true --check prompt_is_input.reply contains "hello John 42"
  ```
  Known failure (reproduced 2026-11-17, [#6752](https://github.com/OpenHands/software-agent-sdk/issues/6752)): with the default tmux pane pool, an `is_input=True` follow-up reaches the running command untouched (`prompt_is_input` passes), but a plain command typed into the running REPL is preceded by Ctrl+C and a `clear` typed into Python, so `repl.reply` starts with `KeyboardInterrupt` and `NameError: name 'clear' is not defined` and `42` falls past the 200 characters the program keeps. The subprocess backend refuses the plain command instead.
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
- **A message from another conversation (`S04.conversation-sender`).** Two persisted conversations; the second gets one plain message and one sent with `sender=conversation_sender(first.id)`, then is closed and reopened from `persistence_dir`. Scripted: nothing runs.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/sender \
    --check prefix eq "conversation:" --check value_is_prefix_plus_hex eq true --check hex_parses eq true \
    --check in_memory len-eq 2 --check in_memory.0.text eq QA_S04_FROM_USER --check in_memory.0.sender eq null \
    --check in_memory.1.text eq QA_S04_FROM_CONVERSATION --check in_memory.1.sender matches '^conversation:[0-9a-f]{32}$' \
    --check after_reload len-eq 2 --check after_reload.0.sender eq null \
    --check after_reload.1.sender matches '^conversation:[0-9a-f]{32}$'
  ```
  Expected: the helper's value is `conversation:` plus the sender's hex id; only the relayed message carries it, in memory and after the reload.
- **Resolution verdicts (`S04.resolution-verdict`).** Fixed judgment sets through `verdict_from_judgments`, no model.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/resolution --arg verdicts \
    --check questions eq '["asks_to_check", "asks_user", "handled", "hands_off_review", "offers_choice", "reports_problem", "task_done"]' \
    --check done.owed eq nothing --check done.resolved eq true --check done.probability eq 0.9 --check done.note eq "0.90 nothing" \
    --check asks.owed eq answer --check asks.needs_user eq true --check asks.probability eq 0.2 \
    --check review.owed eq review --check review.needs_user eq true \
    --check unfinished.owed eq unfinished --check unfinished.needs_user eq false --check unfinished.probability eq 0.4 \
    --check unclear.owed eq unclear --check unclear.resolved eq false --check unclear.probability eq 0.7 \
    --check problem.owed eq check --check problem.needs_user eq false \
    --check out_of_range.owed eq unfinished --check out_of_range.probability eq 0.0
  ```
  Expected: all done with low hand-offs resolves (probability the weakest signal, 0.9); a question, a review, a choice or a check is owed when its judgment holds; unfinished work with no hand-off is `unfinished`; a weak hand-off (0.3) on finished work is `unclear`; a reported problem owes a `check` without needing the user; a non-numeric `task_done` counts as 0.
- **The judge on its saved profile (`S04.resolution-judge`).** An empty profile store and a fallback LLM pointed at a local OpenAI-compatible endpoint (`programs/S16/fake_openai.py`) that answers with fixed JSON (the agent asks the user); then a second fallback, a cipher store, and an endpoint that answers in prose.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S04/resolution --arg judge \
    --check profiles_before len-eq 0 --check profiles_after eq '["resolution_judge.json"]' \
    --check saved.model eq openai/qa-judge-a --check saved.api_key_kept eq true \
    --check judge.usage_id eq resolution_judge --check judge.llm_timeout eq 60 --check judge.llm_num_retries eq 0 \
    --check verdict.owed eq answer --check verdict.needs_user eq true --check verdict.resolved eq false \
    --check verdict.model eq openai/qa-judge-a --check verdict.judgments len-eq 7 \
    --check request.has_state eq true --check request.names_questions eq true \
    --check reused_model eq openai/qa-judge-a \
    --check cipher.plain_key_on_disk eq false --check cipher.loaded_key_matches eq true \
    --check bad_reply_error contains "did not reply with JSON" --check bad_reply_requests eq 1
  ```
  Expected: the first call saves `resolution_judge.json` from the fallback (model and key), the judge runs on it with a 60 s timeout and no LLM retries, and one request carrying the turn and all seven question names yields `owed` `answer` from the profile's model; a later fallback (`qa-judge-b`) does not replace the saved profile; with a cipher the key is not in the file in plain text but loads back; a prose reply raises `ValueError` after one request.
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

- `RemoteConversation.send_message(..., sender=...)` does not record the sender: the server saves the message with `sender: null` ([#7004](https://github.com/OpenHands/software-agent-sdk/issues/7004)); `S04.conversation-sender` holds for local conversations only. Over REST only a conversation sender is recorded (agent-server `F06.send-sender-conversation`).
- `run_goal` and `GoalController.on_run_finished` look for a pending `ask_user` question in the whole event log, not the active branch, so a question left on an abandoned branch still stops a goal ([#6907](https://github.com/OpenHands/software-agent-sdk/issues/6907), `S04.goal-asked-branch`).
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
- `pause()` takes the state lock, so when an LLM call is in flight under a synchronous `run()` it blocks the calling thread until that call (and its tool call) finishes. `arun()` releases the lock while it awaits the model, so there `pause()` returns at once.
- A damaged event file is not skipped on reload: an empty file gives `FileNotFoundError: Missing event file`, invalid JSON a `ValidationError`, and a deleted file `KeyError: 'Unknown event_id: ...'`, so the conversation cannot be reopened.
- `update_secrets`/`secrets=` accept a `str` or a `SecretSource` subclass only; a plain `lambda` raises `ValueError: Invalid SecretValue` although the `update_secrets` docstring speaks of callables. Every `SecretSource` is resolved the first time any command output is masked, not only when a command names it.
- With tmux available the terminal runs in a pane pool and each call first sends Ctrl+C to a command left running by a no-change timeout, so a program waiting on `input()` cannot be answered (`is_input=True` gets `No previous running command to interact with.`); the Python REPL survives only because it ignores Ctrl+C.
- Each terminal starts its own tmux server with a socket under `$TMUX_TMPDIR/tmux-<uid>/`; the socket file stays after `close()` stops the server, so count live servers (`tmux -S <socket> ls`), not files.
- A full disk (`ENOSPC`) during a run: `run()`/`arun()` raise a raw `OSError`, not `ConversationRunError`; no `ConversationErrorEvent` is recorded; `base_state.json` keeps `running` while the in-memory status is `error`; and until space is freed every `with conversation.state:`, read-only ones too, raises `ENOSPC`. Once space returns the state saves `error`, and a reopened conversation runs on without duplicate events ([#7102](https://github.com/OpenHands/software-agent-sdk/issues/7102), `S04.run-error`).
