# Agent step loop

A Python program builds `Agent(llm=..., tools=[...])`, wraps it in `Conversation(agent=..., workspace=...)`, calls `send_message(...)` and then `run()` (or `await arun()`). `run()` repeatedly calls `Agent.step()`: each step builds the LLM message list from the event view (condensing first if the condenser asks), calls `LLM.generate`, and dispatches the reply. Tool calls become `ActionEvent`s that are executed (sequentially by default, or concurrently up to `Agent.tool_concurrency_limit`) and answered by `ObservationEvent`s; a plain text reply or a `FinishAction` sets the status to `FINISHED`; a reasoning-only or empty reply gets a corrective nudge and the loop continues. The loop stops on `FINISHED`, `PAUSED`, `WAITING_FOR_CONFIRMATION`, `STUCK` (the stuck detector saw a repeating pattern), or `ERROR` with a `ConversationErrorEvent` (`MaxIterationsReached` after `max_iteration_per_run` steps, `MaxBudgetReached` after `max_budget_per_run` dollars). Messages sent from another thread while `run()` is busy are picked up by the next step. Images go in as `ImageContent` inside a `Message`; a non-vision model gets a capability message instead of a call. `conversation.ask_agent(question)` asks a side question that leaves no trace in the conversation. A tool spec with `params={"response_schema": Model}` turns that tool's arguments (typically `finish`) into typed structured output.

Source: `openhands-sdk/openhands/sdk/agent/agent.py`, `openhands-sdk/openhands/sdk/agent/base.py`, `openhands-sdk/openhands/sdk/agent/response_dispatch.py`, `openhands-sdk/openhands/sdk/agent/parallel_executor.py`, `openhands-sdk/openhands/sdk/agent/stream_context.py`, `openhands-sdk/openhands/sdk/agent/utils.py`, `openhands-sdk/openhands/sdk/agent/critic_mixin.py`, `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`, `openhands-sdk/openhands/sdk/conversation/stuck_detector.py`, `openhands-sdk/openhands/sdk/conversation/types.py`, `openhands-sdk/openhands/sdk/tool/builtins/finish.py`, `openhands-sdk/openhands/sdk/tool/tool.py`

Needs: `llm`, `network`, `tmux`

Examples: `examples/01_standalone_sdk/01_hello_world.py`, `examples/01_standalone_sdk/17_image_input.py`, `examples/01_standalone_sdk/18_send_message_while_processing.py`, `examples/01_standalone_sdk/20_stuck_detector.py`, `examples/01_standalone_sdk/28_ask_agent_example.py`, `examples/01_standalone_sdk/31_iterative_refinement.py`, `examples/01_standalone_sdk/45_parallel_tool_execution.py`, `examples/01_standalone_sdk/56_structured_output.py`

## Sub-features

- `S01.run-to-finish`: `send_message` + `run()` steps until the agent finishes; status moves IDLE to RUNNING to FINISHED, a second `run()` on a finished conversation returns at once, and a new message moves FINISHED back to IDLE. Unit: `tests/sdk/conversation/local/test_agent_status_transition.py`. Example: `examples/01_standalone_sdk/01_hello_world.py`. Real-LLM: `tests/integration/tests/t01_fix_simple_typo.py`, `tests/integration/tests/t02_add_bash_hello.py`.
- `S01.response-dispatch`: each LLM reply is classified as tool calls, content, reasoning-only or empty; tool calls execute, content sets FINISHED, and the batch's `ActionEvent`s are emitted consecutively. Unit: `tests/sdk/agent/test_response_dispatch.py`. Example: none. Real-LLM: none.
- `S01.empty-reply-nudge`: a reasoning-only or completely empty reply injects a corrective user nudge and the loop continues instead of finishing; the nudge does not reset the stuck-detection window. Unit: `tests/sdk/agent/test_reasoning_only_responses.py`, `tests/sdk/agent/test_tool_call_recovery.py`, `tests/sdk/agent/test_response_dispatch.py::test_corrective_nudge_does_not_reset_stuck_detection_window`. Example: none. Real-LLM: none.
- `S01.max-iterations`: after `max_iteration_per_run` steps (default 500) the run stops with status ERROR and a `ConversationErrorEvent(code="MaxIterationsReached")`, unless the last step finished, which keeps FINISHED. Unit: `tests/sdk/conversation/local/test_agent_status_transition.py::test_execution_status_error_on_max_iterations`, `::test_execution_status_finished_on_final_iteration`. Example: `examples/01_standalone_sdk/28_ask_agent_example.py` (`max_iteration_per_run=5`). Real-LLM: none.
- `S01.max-budget`: with `max_budget_per_run` set, the run stops with ERROR and `MaxBudgetReached` once the combined cost of all the conversation's LLMs reaches it; a finished run stays FINISHED. Unit: `tests/sdk/conversation/local/test_agent_status_transition.py::test_execution_status_error_on_max_budget`, `::test_finished_preserved_even_when_over_budget`. Example: none. Real-LLM: none.
- `S01.stuck-detect`: with `stuck_detection=True` (default) the detector flags repeated action/observation pairs (4), repeated action/error pairs (3), agent monologue (3) and alternating patterns (6) in the events since the last user message, and sets status STUCK. Unit: `tests/cross/test_stuck_detector.py`. Example: `examples/01_standalone_sdk/20_stuck_detector.py`. Real-LLM: none.
- `S01.stuck-thresholds`: `stuck_detection_thresholds` (a `StuckDetectionThresholds` or a dict) overrides the four thresholds. Unit: `tests/cross/test_stuck_detector_config.py`. Example: none. Real-LLM: none.
- `S01.stuck-nudge`: a repeating action-error streak first gets one environment nudge message; the conversation goes STUCK only if the streak continues, and recovers if the model self-corrects. Unit: `tests/sdk/conversation/local/test_stuck_detector_nudge.py`. Example: none. Real-LLM: none.
- `S01.stuck-recover`: `run()` on a STUCK conversation and `send_message` after STUCK both resume the loop. Unit: `tests/sdk/conversation/local/test_agent_status_transition.py::test_run_recovers_from_stuck`, `::test_send_message_resets_stuck_to_idle`. Example: none. Real-LLM: none.
- `S01.parallel-tools`: `Agent(tool_concurrency_limit=N)` (default 1, must be >= 1) runs up to N tool calls of one step concurrently, is faster than sequential, keeps results in call order, and keeps the other results when one call errors. Unit: `tests/sdk/agent/test_parallel_execution_integration.py`, `tests/sdk/agent/test_parallel_executor.py`. Example: `examples/01_standalone_sdk/45_parallel_tool_execution.py`. Real-LLM: none.
- `S01.parallel-resource-locks`: concurrent tools that declare the same resource keys serialize, tools that declare no keys share a per-tool mutex, and an empty key list skips locking. Unit: `tests/sdk/agent/test_parallel_executor_locking.py`. Example: none. Real-LLM: none.
- `S01.finish-truncates-batch`: tool calls after a `finish` call in the same batch are dropped, and a call blocked by a hook produces a rejection in place. Unit: `tests/sdk/agent/test_action_batch.py`, `tests/sdk/agent/test_parallel_execution_integration.py::test_finish_tool_truncates_subsequent_tools`. Example: none. Real-LLM: none.
- `S01.message-while-running`: `send_message` from another thread while `run()` or `arun()` is mid-step is absorbed by the next step instead of being lost or ending the run; a message during a confirmation wait supersedes the pending action. Unit: `tests/sdk/agent/test_message_during_streaming_arun.py`, `tests/sdk/agent/test_message_while_finishing.py`. Example: `examples/01_standalone_sdk/18_send_message_while_processing.py`. Real-LLM: none.
- `S01.async-step`: `arun()`/`astep()` follow the same control flow and release the state lock while the LLM call is in flight, so other threads can read state and send messages. Unit: `tests/sdk/agent/test_astep_releases_state_lock.py`. Example: none. Real-LLM: none.
- `S01.image-input`: a user `Message` with `ImageContent(image_urls=[...])` (http URL or base64 `data:` URL) reaches a vision model; with more than 20 images to an Anthropic model, base64 images are downscaled to 2000x2000 px. Unit: `tests/sdk/llm/test_vision_support.py`, `tests/sdk/llm/test_llm_image_resizing.py`. Example: `examples/01_standalone_sdk/17_image_input.py`. Real-LLM: none (`tests/integration/tests/t08_image_file_viewing.py` covers images read by a tool, not user input).
- `S01.image-non-vision`: images sent to a non-vision model produce an agent message saying the model cannot see images and the run finishes without an LLM call; if a vision profile tool is available the images are replaced by references the agent can inspect with that tool. Unit: `tests/sdk/agent/test_non_multimodal_image_input.py`. Example: none. Real-LLM: none.
- `S01.ask-agent`: `conversation.ask_agent(question)` returns a direct LLM answer built from the current history, adds no events and leaves status unchanged, works while `run()` is in flight, drops unanswered tool calls from the context, and raises on context-window errors. Unit: `tests/sdk/conversation/test_ask_agent.py`. Example: `examples/01_standalone_sdk/28_ask_agent_example.py`. Real-LLM: none.
- `S01.structured-output`: `Tool(name=..., params={"response_schema": Model})` extends that tool's action schema with the model's fields, validates the LLM's arguments against it, and `tool.parse_last_response(events)` returns the latest typed instance. Invalid schemas (object-level constraints, dynamic fields, field collisions, reserved names, toolsets) are rejected. Unit: `tests/sdk/tool/test_response_schema.py`. Example: `examples/01_standalone_sdk/56_structured_output.py`. Real-LLM: none.
- `S01.malformed-tool-call`: malformed tool arguments are repaired when possible (control characters, stringified JSON); a native tool call that still fails validation becomes an `AgentErrorEvent` naming the validation error (a malformed prompted-mode `<function=...>` reply becomes a user `MessageEvent` instead) and the loop continues. Unit: `tests/sdk/agent/test_fix_malformed_tool_arguments.py`, `tests/sdk/agent/test_tool_validation_error_message.py`. Example: none. Real-LLM: none.
- `S01.unknown-tool`: a call to a tool the agent does not have yields an `AgentErrorEvent` the model sees, not a crash. Unit: `tests/sdk/agent/test_nonexistent_tool_handling.py`. Example: none. Real-LLM: none.
- `S01.tool-exception`: an exception inside a tool executor becomes an `AgentErrorEvent` for that call and the conversation continues. Unit: `tests/sdk/agent/test_tool_execution_error_handling.py`. Example: none. Real-LLM: none.
- `S01.content-policy`: an output blocked by the provider's content filter becomes a user nudge to rephrase, not a fatal error. Unit: `tests/sdk/agent/test_agent_content_policy_violation.py`. Example: none. Real-LLM: none.
- `S01.context-overflow`: a context-window or malformed-history error from the LLM emits a `CondensationRequest` when the condenser handles requests, and is raised otherwise. Unit: `tests/sdk/agent/test_agent_context_window_condensation.py`. Example: none. Real-LLM: `tests/integration/tests/c02_hard_context_reset.py`.
- `S01.iterative-refinement`: a program-level loop of fresh conversations (refactor, then critique, then refactor with feedback) until a score threshold or an iteration cap is met. Critic-driven refinement inside one conversation is family S12. Unit: **none (untested)**. Example: `examples/01_standalone_sdk/31_iterative_refinement.py`. Real-LLM: none.

## How to get to it (agent POV)

- `Conversation(agent=..., workspace=..., callbacks=[...], max_iteration_per_run=500, stuck_detection=True, stuck_detection_thresholds=None, max_budget_per_run=None)` returns a `LocalConversation` for a local workspace; `conversation.run()` / `await conversation.arun()` drive the loop. Covers `S01.run-to-finish`, `S01.max-*`, `S01.stuck-*`, `S01.async-step`.
- `conversation.state.execution_status` (`ConversationExecutionStatus`: IDLE, RUNNING, PAUSED, WAITING_FOR_CONFIRMATION, FINISHED, ERROR, STUCK) and `ConversationErrorEvent.code` are the observable outcome of a run; `conversation.stuck_detector.is_stuck()` re-checks on demand.
- `Agent(llm=..., tools=[Tool(name=TerminalTool.name), ...], tool_concurrency_limit=1, condenser=..., critic=..., include_default_tools=[...])`; `get_default_agent(llm=...)` from `openhands.tools.preset.default` gives the stock tool set. Covers `S01.parallel-*`, `S01.finish-truncates-batch`, `S01.context-overflow`.
- `conversation.send_message(str | Message)` from any thread, including while `run()` runs in a `threading.Thread`. Covers `S01.message-while-running`.
- `Message(role="user", content=[TextContent(...), ImageContent(image_urls=[...])])`; `llm.vision_is_active()` tells whether images will be sent; `LLM(disable_vision=True)` forces text-only. Covers `S01.image-*`.
- `conversation.ask_agent("question")` returns `str`. Covers `S01.ask-agent`.
- `register_tool("FinishTool", FinishTool)` then `Tool(name="FinishTool", params={"response_schema": MyModel})`; read back with `agent.tools_map["finish"].parse_last_response(conversation.state.events)`. Covers `S01.structured-output`.
- `Agent.step()` / `Agent.astep()` are what `run()` calls; custom agents subclass `AgentBase` and implement `step`.

## Driving it

Preconditions:
- `uv sync --dev` has run; `control-sdk` is on `PATH` (`export PATH="$PWD/.agents/skills/verify-sdk/scripts:$PATH"`). `sdk-map run` does both.
- `tmux` on `PATH` (the conversations use `TerminalTool`).
- Live bullets: `DEEPSEEK_API_KEY` exported.

Scripted bullets use the SDK's `TestLLM` (`--script`, or `make_llm(script=...)` in `programs/S01/loop.py`), so the real agent loop, real tools and real event log run with no key. Where cost or the wire format matters the program points a real `LLM` at `programs/S02/fake_provider.py`. `programs/S01/sleeper.py` is a small custom tool (waits, records when it ran, can declare resource keys or raise).

- **Run to finish, and what a finished conversation does next (`S01.run-to-finish`, `S01.response-dispatch`).** A terminal call, then a plain text reply (content finishes the run); a second `run()` makes no LLM call; a new message moves FINISHED back to IDLE.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  CID=$(control-sdk conversation new --script S01/run_to_finish.json --field id)
  control-sdk conversation send "$CID" --text "Write result.txt" --run \
    --check execution_status eq finished --check last_agent_message eq "result.txt now says done."
  control-sdk exec --expect-output done -- cat result.txt
  control-sdk conversation run "$CID" --check execution_status eq finished --check script_left eq 0
  control-sdk conversation send "$CID" --text "One more thing" --check execution_status eq idle
  control-sdk program S01/loop --arg status \
    --check initial eq idle --check observed contains running --check after_run eq finished \
    --check rerun_llm_calls eq 0 --check after_rerun eq finished --check after_new_message eq idle
  ```
- **Parallel tool calls are emitted together (`S01.response-dispatch`).** One LLM reply with two tool calls produces two consecutive `ActionEvent`s before their observations.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  CID=$(control-sdk conversation new --script S01/echo_parallel_finish.json --field id)
  control-sdk conversation send "$CID" --text "Write greeting.txt and check it" --run --check execution_status eq finished
  control-sdk conversation events "$CID" --last 6 \
    --check kinds.0 eq ActionEvent --check kinds.1 eq ActionEvent \
    --check kinds.2 eq ObservationEvent --check kinds.3 eq ObservationEvent \
    --check events.0.tool eq file_editor --check events.1.tool eq terminal
  ```
- **Recovering inside a step (`S01.empty-reply-nudge`, `S01.malformed-tool-call`, `S01.unknown-tool`, `S01.content-policy`).** One scripted run meets an unknown tool, arguments that fail validation, a stringified JSON list (repaired), a content-filter block and an empty reply, then finishes.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  CID=$(control-sdk conversation new --script S01/step_errors.json --field id)
  control-sdk conversation send "$CID" --text "Plan the work" --run \
    --check execution_status eq finished --check last_agent_message eq "survived every error"
  control-sdk conversation events "$CID" --kind AgentErrorEvent \
    --check count eq 2 --check events.0.error contains "Tool 'no_such_tool' not found" \
    --check events.1.error contains "Error validating tool 'terminal'"
  control-sdk conversation events "$CID" --kind ObservationEvent \
    --check events.0.tool eq task_tracker --check events.0.is_error eq false --check events.0.text contains "1 item"
  control-sdk conversation events "$CID" --kind MessageEvent --source user \
    --check events.1.text contains "blocked by the model's content filter"
  control-sdk conversation events "$CID" --kind MessageEvent --source environment \
    --check count eq 1 --check events.0.text contains "did not include a function call"
  ```
- **A reasoning-only reply (`S01.empty-reply-nudge`).** A reply with `reasoning_content` and no text or tool call gets the same nudge.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S01/loop --arg reasoning-only \
    --check status eq finished --check llm_calls eq 2 --check nudges eq 1 --check nudge_text contains "did not include a function call"
  ```
- **The iteration cap (`S01.max-iterations`).** `max_iteration_per_run=2` with three steps of work, and with the finish on step two.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  CID=$(control-sdk conversation new --script S01/max_iterations.json --max-iterations 2 --field id)
  control-sdk conversation send "$CID" --text "Echo three times" --run --allow-error \
    --check execution_status eq error --check script_left eq 2
  control-sdk conversation events "$CID" --kind ConversationErrorEvent --check count eq 1 --check events.0.code eq MaxIterationsReached
  CID=$(control-sdk conversation new --script S01/finish_on_last.json --max-iterations 2 --field id)
  control-sdk conversation send "$CID" --text "Echo once" --run --check execution_status eq finished
  ```
- **The budget cap (`S01.max-budget`).** A priced model (0.14 per call) under `max_budget_per_run=0.2`, and a run that finishes on its first call over a 0.1 budget.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S01/loop --arg budget \
    --check over_budget.status eq error --check over_budget.codes.0 eq MaxBudgetReached \
    --check over_budget.llm_calls eq 2 --check over_budget.cost eq 0.28 \
    --check finish_over_budget.status eq finished --check finish_over_budget.codes len-eq 0 \
    --check factory_accepts_budget eq true
  ```
  Known failure (reproduced 2026-10-13, not yet filed): the budget logic works on `LocalConversation(..., max_budget_per_run=...)`, but the public `Conversation(...)` factory does not take the argument this family documents: `TypeError: Conversation.__new__() got an unexpected keyword argument 'max_budget_per_run'` (`factory_accepts_budget` is `false`). A program has to import `LocalConversation` from `openhands.sdk.conversation.impl.local_conversation` to set a budget.
- **Stuck on a repeating action (`S01.stuck-detect`, `S01.stuck-recover`).** Five identical `ls` calls: the fourth pair sets STUCK; a new message moves it to IDLE and the run then reaches the script's `finish`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  CID=$(control-sdk conversation new --script S01/repeat_ls.json --field id)
  control-sdk conversation send "$CID" --text "List files" --run --check execution_status eq stuck --check script_left eq 2
  control-sdk conversation send "$CID" --text "Try something else" --check execution_status eq idle
  control-sdk conversation run "$CID" --check execution_status eq finished --check last_agent_message eq recovered
  ```
- **`run()` alone on a stuck conversation (`S01.stuck-recover`).** The same pattern, resumed with `run()` and no new message.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  CID=$(control-sdk conversation new --script S01/repeat_ls.json --field id)
  control-sdk conversation send "$CID" --text "List files" --run --check execution_status eq stuck
  control-sdk conversation run "$CID" --check script_left lt 2
  ```
  Known failure (reproduced 2026-10-13, not yet filed): `run()` resets STUCK to RUNNING, but the detector sees the same repeated pairs since the last user message before any LLM call and sets STUCK again (`script_left` stays 2, status `stuck`). Only a new user message gets a detected-stuck conversation moving; the unit test `test_run_recovers_from_stuck` sets STUCK by hand with no repeating events, so it never meets this.
- **Custom thresholds (`S01.stuck-thresholds`).** Three identical `ls` calls with the defaults (no stuck) and with `stuck_detection_thresholds={"action_observation": 2}`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S01/loop --arg thresholds \
    --check default.status eq finished --check default.llm_calls eq 4 \
    --check custom.status eq stuck --check custom.llm_calls eq 2
  ```
- **The repeated-error nudge (`S01.stuck-nudge`).** Three identical calls to a missing tool get one environment nudge; the model then finishes and the run is not STUCK.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  CID=$(control-sdk conversation new --script S01/repeat_error.json --field id)
  control-sdk conversation send "$CID" --text "Do it" --run \
    --check execution_status eq finished --check last_agent_message eq "switched approach"
  control-sdk conversation events "$CID" --kind MessageEvent --source environment \
    --check count eq 1 --check events.0.text contains "3 times in a row"
  ```
- **Concurrent tool calls (`S01.parallel-tools`, `S01.finish-truncates-batch`).** Four one-second `sleeper` calls in one reply with `tool_concurrency_limit=1` and `4`; a batch where one call raises; a batch with `finish` in the middle.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S01/loop --arg parallel \
    --check sequential_seconds ge 4 --check parallel_seconds lt 2 --check parallel_overlap eq true \
    --check observation_order.0 eq s0 --check observation_order.3 eq s3 \
    --check with_failure.status eq finished --check with_failure.results.0 eq ObservationEvent:ok1 \
    --check with_failure.results.1 eq AgentErrorEvent:sleeper --check with_failure.results.2 eq ObservationEvent:ok2 \
    --check finish_batch.status eq finished --check finish_batch.ran len-eq 1 --check finish_batch.ran.0 eq before \
    --check limit_zero_rejected eq true
  ```
  Results stay in call order even when they finish out of order; the failing call becomes an `AgentErrorEvent` and the others keep their results; the call after `finish` never runs.
- **Resource locks (`S01.parallel-resource-locks`).** Pairs of concurrent `sleeper` calls: same key, different keys, no declaration (per-tool mutex), and declared with no keys.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S01/loop --arg locks \
    --check same_key_overlap eq false --check different_keys_overlap eq true \
    --check undeclared_overlap eq false --check declared_no_keys_overlap eq true
  ```
- **An exception inside a tool (`S01.tool-exception`).**
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S01/loop --arg tool-exception \
    --check status eq finished --check agent_errors eq 1 --check error_tool eq sleeper \
    --check error_text contains "failed on purpose"
  ```
- **A message sent while the agent works (`S01.message-while-running`).** `run()` in a thread, the terminal sleeping two seconds, a second message sent from the main thread.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S01/loop --arg message-while-running \
    --check status_mid_run eq running --check status eq finished --check user_messages len-eq 2 \
    --check second_call_saw contains pineapple --check llm_calls eq 2
  ```
- **The async loop (`S01.async-step`, `S01.message-while-running`).** `arun()` with an LLM call that takes two seconds; the program takes the state lock and sends a message while the call is in flight.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S01/loop --arg async \
    --check lock_wait_seconds lt 0.5 --check status_mid_call eq running --check status eq finished \
    --check actions.0 eq terminal --check actions.1 eq finish --check user_messages eq 2
  ```
- **Images (`S01.image-input`, `S01.image-non-vision`).** A URL and a `data:` image to a vision model; 21 and 2 large base64 images to an Anthropic model; an image to a model without vision.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S01/loop --arg image-input \
    --check openai.vision_active eq true --check openai.status eq finished \
    --check openai.image_urls.0 matches '^https://' --check openai.image_urls.1 matches '^data:image/png' \
    --check anthropic_21_images.images eq 21 --check anthropic_21_images.max_side le 2000 \
    --check anthropic_2_images.max_side eq 2600
  control-sdk program S01/loop --arg image-non-vision \
    --check vision_active eq false --check status eq finished --check llm_calls eq 0 \
    --check agent_reply contains "does not support image"
  ```
  The vision-profile substitution needs a vision profile tool configured; it is not driven here.
- **Side questions (`S01.ask-agent`).** `ask_agent` while `run()` is in flight, after it ends, and when the side call overflows the context.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S01/loop --arg ask-agent \
    --check during_run.answer contains "answer from" --check during_run.status eq running \
    --check after_run.answer contains "answer from" --check after_run.events_added eq 0 \
    --check after_run.status_after eq finished --check no_side_question_in_events eq true \
    --check context_error_raised eq LLMContextWindowExceedError \
    --check run_actions.0 eq terminal --check registry contains ask-agent-llm
  ```
- **Structured output (`S01.structured-output`).** `response_schema` on `TerminalTool` and on `FinishTool`; two invalid schemas.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S01/loop --arg structured-output \
    --check status eq finished --check facts_type eq ProjectFacts --check facts len-eq 3 \
    --check rationale_type eq CommandRationale --check rationale_purpose eq "see the files" \
    --check finish_schema_fields contains facts --check finish_schema_fields contains message \
    --check rejected.reserved_name eq ValueError --check rejected.field_collision eq ValueError
  ```
- **Context overflow (`S01.context-overflow`).** A context-window error with an `LLMSummarizingCondenser` (emits a `CondensationRequest` and the run continues) and without one (the run ends in ERROR).
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S01/loop --arg context-overflow \
    --check with_condenser.status eq finished --check with_condenser.condensation_requests eq 1 \
    --check without_condenser.status eq error --check without_condenser.raised eq ConversationRunError \
    --check without_condenser.codes.0 eq LLMContextWindowExceedError
  ```
- **The loop with a real model (`S01.run-to-finish`, `S01.response-dispatch`, `S01.ask-agent`).** Live.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  CID=$(control-sdk conversation new --field id)
  control-sdk conversation send "$CID" --run \
    --text "Using the terminal, create facts.txt containing the line 'loop ok', then finish." \
    --check execution_status eq finished
  control-sdk exec --expect-output "loop ok" -- cat facts.txt
  control-sdk exec --model-env --expect-output "All done" -- python examples/01_standalone_sdk/01_hello_world.py
  control-sdk exec --model-env --expect-output EXAMPLE_COST -- python examples/01_standalone_sdk/28_ask_agent_example.py
  ```
- **Examples 18, 20, 45 and 56 (`S01.message-while-running`, `S01.stuck-detect`, `S01.parallel-tools`, `S01.structured-output`).** Live; example 20 prints `Final stuck status: False` when the model refuses to repeat itself, so only its completion is checked.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output EXAMPLE_COST -- python examples/01_standalone_sdk/18_send_message_while_processing.py
  control-sdk exec --model-env --expect-output "Final stuck status" -- python examples/01_standalone_sdk/20_stuck_detector.py
  control-sdk exec --model-env --expect-output EXAMPLE_COST -- python examples/01_standalone_sdk/45_parallel_tool_execution.py
  control-sdk exec --model-env --expect-output EXAMPLE_COST -- python examples/01_standalone_sdk/56_structured_output.py
  ```
- **Iterative refinement (`S01.iterative-refinement`).** Live; several full conversations per round.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --env MAX_ITERATIONS=2 --env PHASE_MAX_ITERATIONS=15 --expect-output EXAMPLE_COST -- python examples/01_standalone_sdk/31_iterative_refinement.py
  ```
- **Real-model task runs (`S01.run-to-finish`, `S01.context-overflow`).** Live, through the integration runner.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env -- python tests/integration/run_infer.py --llm-config '{"model": "deepseek/deepseek-flash"}' --eval-ids t01_fix_simple_typo,t02_add_bash_hello
  ```
- **Example 17, image input on a real model (`S01.image-input`), blocked.** The example asserts `llm.vision_is_active()`; the run's DeepSeek model has no vision, so it needs a vision model key (and network to python.org).

## Gotchas

- `01_hello_world.py` is in `_EXCLUDED_EXAMPLES` in `tests/examples/test_examples.py`, so the `test-examples` label never runs it; the other seven examples here do run there (`pytest tests/examples/test_examples.py --run-examples`).
- All examples read `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL`; `56_structured_output.py` defaults `LLM_MODEL` to `anthropic/claude-sonnet-4-5-20250929`, the rest to `gpt-5.5`. `17_image_input.py` asserts `llm.vision_is_active()` and fails fast on a text-only model; it also fetches an image from python.org.
- Examples run with the repo root as workspace and write files there (`FACTS.txt`, `image_report.md`, the document from example 18).
- `31_iterative_refinement.py` reads `QUALITY_THRESHOLD`, `MAX_ITERATIONS`, `PHASE_MAX_ITERATIONS`, `COBOL_SAMPLE_LIMIT`; it is several full conversations per iteration and the slowest example here.
- `max_iteration_per_run` counts steps, not tool calls; a parallel batch of tool calls is one step. Hitting the cap is status ERROR, not FINISHED.
- `stuck_detection` is on by default. A test or example that deliberately repeats an action will go STUCK (after one nudge for repeating errors).
- `tool_concurrency_limit > 1` shares the conversation, filesystem and working directory between concurrent tools; races on shared state are the caller's problem unless the tool declares resource keys.
- `ask_agent` copies the agent's LLM with streaming disabled; it is registered in `conversation.llm_registry` under usage id `ask-agent-llm`, so its cost shows up in the conversation stats separately from the agent's.
- Example 18 and 28 run `conversation.run()` in a `threading.Thread`; join the thread before reading final state.
- The `Conversation(...)` factory does not accept `max_budget_per_run` (`TypeError: Conversation.__new__() got an unexpected keyword argument`); only `LocalConversation(...)` takes it.
- `ConversationStateUpdateEvent`s are not delivered to `callbacks` in a local run; poll `conversation.state.execution_status` (from another thread) to watch status changes.
- Once a run has ended STUCK, calling `run()` again is re-detected as STUCK straight away with no LLM call; only a new user message lets the agent continue.
- `ConversationErrorEvent` is not exported from `openhands.sdk.event`; import it from `openhands.sdk.event.conversation_error`.
