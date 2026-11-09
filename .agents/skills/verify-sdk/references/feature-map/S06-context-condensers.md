# Agent context, system prompt, condensers and memory

An SDK user shapes what the model sees in three places. `Agent(agent_context=AgentContext(...))` adds skills (triggered knowledge appended to a user message, or always-on repo skills in the system prompt), a `system_message_suffix` and `user_message_suffix`, the names and descriptions of available secrets, the current date and time, and, with `load_memory=True`, the agent-maintained `MEMORY.md` indexes from `~/.openhands/memory/` and `<workspace>/.openhands/memory/`. The system prompt itself comes from a registry of prompt sections (default or planning preset, model-specific sections for GPT-5 and Codex, vendor files for Claude and Gemini, an identity line from `~/.openhands/SOUL.md`) or from `Agent(system_prompt=...)` / `system_prompt_filename=...` / `system_prompt_kwargs=...`, and is emitted once as a `SystemPromptEvent` split into a static, cacheable block and a dynamic block. `Agent(condenser=...)` keeps long conversations within limits: before each LLM call the condenser looks at the current `View` of events and either passes it through or emits a `Condensation` event that forgets a range of events and inserts a summary. `LLMSummarizingCondenser(llm=..., max_size=240, max_tokens=None, keep_first=2)` summarizes with a separate LLM when the event count, the token count or an explicit `CondensationRequest` (from `conversation.condense()` or a context-window error) calls for it, and falls back to a hard context reset if summarizing fails; `NoOpCondenser` never condenses; `PipelineCondenser` chains condensers. Condensation never splits an action from its observation, a parallel batch, or an Anthropic thinking tool loop.

Source: `openhands-sdk/openhands/sdk/context/agent_context.py`, `openhands-sdk/openhands/sdk/context/memory.py`, `openhands-sdk/openhands/sdk/context/prompts/`, `openhands-sdk/openhands/sdk/context/condenser/`, `openhands-sdk/openhands/sdk/context/view/`, `openhands-sdk/openhands/sdk/event/condenser.py`, `openhands-sdk/openhands/sdk/agent/base.py`, `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`

Needs: `llm`, `tmux`

Examples: `examples/01_standalone_sdk/14_context_condenser.py`, `examples/01_standalone_sdk/55_persistent_memory.py`, `examples/01_standalone_sdk/03_activate_skill.py`

## Sub-features

- `S06.context-suffixes`: `AgentContext(system_message_suffix=..., user_message_suffix=...)` appends text to the system prompt and to user messages; empty values add nothing. Unit: `tests/sdk/context/test_agent_context.py`. Example: `examples/01_standalone_sdk/03_activate_skill.py`. Real-LLM: none.
- `S06.context-skills`: repo skills (no triggers) go into the system prompt and triggered skills into the user message whose text matches a trigger (case-insensitive), with already-activated skills skipped; `disabled_skills` drops named skills and duplicate names are rejected. Skill loading and discovery are family S07. Unit: `tests/sdk/context/test_agent_context.py`. Example: `examples/01_standalone_sdk/03_activate_skill.py`. Real-LLM: `tests/integration/tests/t09_invoke_skill.py`.
- `S06.context-secrets`: secret names and descriptions (never values) from `AgentContext(secrets=...)` and the conversation's secret registry are listed in the system prompt; raw string secrets are redacted on serialization and round-trip through a cipher. Unit: `tests/sdk/context/test_agent_context.py::test_get_system_message_suffix_with_secrets_only`, `tests/sdk/context/test_agent_context_serialization.py`, `tests/sdk/context/prompts/test_prompt_snapshot.py::test_dynamic_context_with_secret_registry`. Example: none. Real-LLM: none.
- `S06.context-datetime`: `current_datetime` (a `datetime` or a string; defaults to now) is rendered in the dynamic part of the system prompt. Unit: `tests/sdk/context/test_agent_context.py::test_get_system_message_suffix_with_datetime_only`, `::test_agent_context_default_datetime`. Example: none. Real-LLM: none.
- `S06.system-prompt-registry`: the built-in prompt is assembled from registered sections (default and planning presets), its rendering for fixed inputs matches checked-in snapshots, and the static block has no dynamic content. Unit: `tests/sdk/context/prompts/test_prompt_snapshot.py`, `tests/sdk/context/prompts/test_registry.py`, `tests/sdk/context/prompts/test_default_registry.py`, `tests/sdk/context/prompts/test_planning_registry.py`, `tests/sdk/context/prompts/test_section.py`. Example: none. Real-LLM: none.
- `S06.system-prompt-model-specific`: GPT-5 and GPT-5 Codex models get their own prompt section (detected from the canonical model name or a variant override); Claude and Gemini vendor files are included only for their models. Unit: `tests/sdk/context/test_prompt_model_spec.py`, `tests/sdk/context/test_agent_context_model_specific.py`. Example: none. Real-LLM: none.
- `S06.system-prompt-custom`: `Agent(system_prompt=...)` replaces the built-in prompt; `system_prompt_filename` accepts a relative or absolute template path (missing files raise); `system_prompt_kwargs` feed the template. Unit: `tests/sdk/context/test_prompt_absolute_path.py`, `tests/sdk/context/prompts/test_persona.py::test_inline_system_prompt_wins_over_persona`. Example: none. Real-LLM: none.
- `S06.persona-soul`: a persona replaces the persona sections and keeps capability and dynamic sections; the identity line comes from `~/.openhands/SOUL.md` when present, else a built-in default. Unit: `tests/sdk/context/prompts/test_persona.py`, `tests/sdk/context/prompts/test_prompt_snapshot.py::test_soul_default_snapshot`, `::test_soul_custom_snapshot`. Example: none. Real-LLM: none.
- `S06.memory-load`: with `AgentContext(load_memory=True)` the first `send_message()`/`run()` reads the user and project `MEMORY.md` indexes (user tier first) into the system prompt, truncating whole lines from the top past 6000 characters; empty, unreadable or missing files are treated as absent and never block startup; daily logs are not loaded. Unit: `tests/sdk/context/test_memory.py`, `tests/sdk/conversation/test_local_conversation_memory.py`. Example: `examples/01_standalone_sdk/55_persistent_memory.py`. Real-LLM: none.
- `S06.memory-write-path`: the prompt tells the agent where to write memory, and that path is the same one the loader reads (honoring `OH_PERSISTENCE_DIR`); the resolved memory text is not serialized with the `AgentContext`. Unit: `tests/sdk/context/test_memory.py::test_instructed_write_path_matches_loader_read_path_with_persistence_dir`, `tests/sdk/context/test_agent_context_serialization.py::test_memory_context_excluded_from_serialization`. Example: `examples/01_standalone_sdk/55_persistent_memory.py`. Real-LLM: none.
- `S06.view`: the `View` the condenser sees hides forgotten events, inserts the summary at `summary_offset`, keeps several summaries in order, and always drops `CondensationRequest` events. Unit: `tests/sdk/context/view/test_view.py`, `tests/sdk/context/view/test_view_multi_summary.py`, `tests/sdk/context/view/test_view_append_event.py`. Example: none. Real-LLM: none.
- `S06.view-atomicity`: forgetting never splits an action from its observation, a parallel tool batch, or a tool loop (needed for Anthropic thinking blocks), and observations stay unique. Unit: `tests/sdk/context/view/properties/`, `tests/sdk/context/view/test_view_batch_atomicity.py`, `tests/sdk/context/view/test_view_condensation_batch_atomicity.py`, `tests/sdk/context/view/test_view_tool_loop_boundaries.py`, `tests/sdk/context/view/test_manipulation_indices.py`. Example: none. Real-LLM: `tests/integration/tests/c01_thinking_block_condenser.py`.
- `S06.summarizing-size`: `LLMSummarizingCondenser(max_size=N, keep_first=K)` condenses once the view exceeds N events, keeping the first K events and a tail, and summarizes the forgotten middle (building on the previous summary); `keep_first` must be below `max_size // 2`. Unit: `tests/sdk/context/condenser/test_llm_summarizing_condenser.py`. Example: `examples/01_standalone_sdk/14_context_condenser.py`. Real-LLM: `tests/integration/tests/c05_size_condenser.py`.
- `S06.summarizing-tokens`: with `max_tokens` (or the agent LLM's effective input limit, whichever is smaller) the condenser triggers on token count and forgets enough events to get under it. Unit: `tests/sdk/context/condenser/test_llm_summarizing_condenser.py::test_condense_with_token_limit_exceeded`, `::test_token_limit_uses_stricter_configured_or_agent_limit`, `tests/sdk/context/condenser/test_utils.py`. Example: none. Real-LLM: `tests/integration/tests/c04_token_condenser.py`.
- `S06.summarizing-reasons`: request, event-count and token reasons combine and the most aggressive condensation wins; `minimum_progress` (default 0.1) rejects condensations that forget too little. Unit: `tests/sdk/context/condenser/test_llm_summarizing_condenser.py::test_most_aggressive_condensation_chosen`, `::test_minimum_progress_threshold_not_met`. Example: none. Real-LLM: none.
- `S06.soft-vs-hard`: a soft requirement (size) with no valid range to forget leaves the view unchanged and the run continues; a hard requirement (explicit request, token pressure) with no valid range triggers a hard context reset. Unit: `tests/sdk/context/condenser/test_llm_summarizing_condenser.py::test_condense_with_soft_requirement_and_no_condensation_available`, `::test_condense_with_hard_requirement_and_no_condensation_available`, `tests/sdk/context/condenser/test_rolling_condenser.py`. Example: none. Real-LLM: `tests/integration/tests/c03_delayed_condensation.py`.
- `S06.hard-reset`: when summarizing fails (LLM error or invalid range) the condenser retries a hard context reset up to `hard_context_reset_max_retries` (5), shrinking event text by `hard_context_reset_context_scaling` (0.8) each time, and keeps the leading system prompt. Unit: `tests/sdk/context/condenser/test_llm_summarizing_condenser.py::test_llm_error_triggers_hard_context_reset`, `::test_hard_context_reset_preserves_leading_system_prompt`, `tests/sdk/context/condenser/test_rolling_condenser.py::test_hard_context_reset_condensation_is_returned`. Example: none. Real-LLM: `tests/integration/tests/c02_hard_context_reset.py`.
- `S06.summary-llm-streaming`: the summarizing LLM is a separate `LLM` (own `usage_id` for cost tracking); streaming is switched off for summaries unless the provider requires it, and `extra_body` is not passed. Unit: `tests/sdk/context/condenser/test_llm_summarizing_condenser.py::test_summarization_disables_streaming_when_llm_streams`, `::test_get_condensation_does_not_pass_extra_body`. Example: `examples/01_standalone_sdk/14_context_condenser.py`. Real-LLM: none.
- `S06.manual-condense`: `conversation.condense()` adds a `CondensationRequest` and runs one step to process it; it raises `ValueError` with setup instructions when there is no condenser or the condenser does not handle requests. Unit: `tests/sdk/conversation/test_condense.py`. Example: none. Real-LLM: none.
- `S06.context-overflow-request`: a context-window or malformed-history error from the LLM becomes a `CondensationRequest` when the condenser handles requests, and is raised (with a warning) otherwise. Unit: `tests/sdk/agent/test_agent_context_window_condensation.py`. Example: none. Real-LLM: `tests/integration/tests/c02_hard_context_reset.py`. (Also listed as `S01.context-overflow`.)
- `S06.condenser-resume`: the state's cached `View` applies condensations incrementally and matches a full rebuild, and the resume path rebuilds it from the reloaded `Condensation` events. Unit: `tests/sdk/conversation/test_state_view_cache.py`. Example: `examples/01_standalone_sdk/14_context_condenser.py`. Real-LLM: none.
- `S06.noop-condenser`: `NoOpCondenser` returns the view unchanged, with or without an agent LLM. Unit: `tests/sdk/context/condenser/test_no_op_condenser.py`. Example: none. Real-LLM: none.
- `S06.pipeline-condenser`: `PipelineCondenser(condensers=[...])` runs condensers in order, stops at the first `Condensation`, and handles requests if any member does. Unit: **none (untested)**. Example: none. Real-LLM: none.

## How to get to it (agent POV)

- `Agent(llm=..., tools=..., agent_context=AgentContext(...))` with `skills`, `system_message_suffix`, `user_message_suffix`, `secrets`, `current_datetime`, `disabled_skills`, `load_user_skills`, `load_public_skills`, `load_project_skills`, `load_memory`: `S06.context-*`, `S06.memory-*`.
- `Agent(system_prompt=..., system_prompt_filename="system_prompt.j2" | "system_prompt_planning.j2" | path, system_prompt_kwargs={...})`: `S06.system-prompt-*`. Files: `~/.openhands/SOUL.md` (`S06.persona-soul`), `~/.openhands/memory/MEMORY.md` and `<workspace>/.openhands/memory/MEMORY.md` (`S06.memory-load`). `OH_PERSISTENCE_DIR` replaces `~/.openhands`.
- `Agent(condenser=LLMSummarizingCondenser(llm=llm.model_copy(update={"usage_id": "condenser"}), max_size=..., max_tokens=..., keep_first=..., minimum_progress=..., hard_context_reset_max_retries=..., hard_context_reset_context_scaling=...))`: `S06.summarizing-*`, `S06.soft-vs-hard`, `S06.hard-reset`, `S06.summary-llm-streaming`.
- `from openhands.sdk.context.condenser import NoOpCondenser, PipelineCondenser, LLMSummarizingCondenser, CondenserBase, RollingCondenser`: `S06.noop-condenser`, `S06.pipeline-condenser`; subclass `RollingCondenser` for a custom condenser.
- `conversation.condense()`: `S06.manual-condense`. Events: `Condensation(forgotten_event_ids, summary, summary_offset)`, `CondensationRequest`, `CondensationSummaryEvent` in `openhands.sdk.event`: `S06.view`.
- Settings path: `OpenHandsAgentSettings(condenser=LLMSummarizingCondenserSettings(enabled=True, max_size=...))` builds the same condenser (family S19).

## Driving it

Preconditions:
- `uv sync --dev` has run; `control-sdk` is on `PATH` (`export PATH="$PWD/.agents/skills/verify-sdk/scripts:$PATH"`). `sdk-map run` does both.
- `tmux` on `PATH` (the condenser conversations use `TerminalTool`).
- Live bullets: `DEEPSEEK_API_KEY` exported.

The programs under `programs/S06/` record every scripted LLM call (`recorder.py`, a `TestLLM` subclass), so checks read the exact system blocks, user messages and summary prompts the SDK sent.

- **What the model is sent from an `AgentContext` (`S06.context-suffixes`, `S06.context-skills`, `S06.context-secrets`, `S06.context-datetime`).** A conversation whose context has an always-on skill, a keyword skill, a disabled skill, both suffixes, two secrets and a fixed date runs three scripted turns: the keyword (in another case), the keyword again, and a plain message.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S06/context_prompt \
    --check llm_calls eq 3 --check system_blocks eq 2 \
    --check static_has_sys_suffix eq false --check dynamic_has_sys_suffix eq true \
    --check repo_skill_in_dynamic eq true --check repo_skill_in_static eq false \
    --check disabled_skill_in_prompt eq false --check keyword_skill_body_in_system eq false \
    --check keyword_skill_listed eq true \
    --check secret_names_in_dynamic len-eq 2 --check secret_description_in_dynamic eq true \
    --check secret_value_sent eq false --check secret_in_plain_dump eq false \
    --check secret_in_cipher_dump eq false --check secret_after_cipher_roundtrip eq true \
    --check secret_in_persisted_state eq false \
    --check datetime_in_dynamic eq true --check datetime_in_static eq false \
    --check turn1_has_skill eq true --check turn1_has_user_suffix eq true \
    --check turn2_has_skill eq false --check turn2_has_user_suffix eq true \
    --check turn3_has_user_suffix eq true --check activated_skills len-eq 1 \
    --check activated_skills.0 eq cat-voice --check duplicate_rejected eq true \
    --check blank_user_suffix missing
  ```
  The system message has two blocks: the static prompt, then the dynamic block with the repo skill, the `<available_skills>` entry for the keyword skill, the secret names and description (never a value), the date and the suffix. The keyword skill's body arrives once, appended to the first user message that matches; the user suffix is on every turn.
- **Which system prompt is built (`S06.system-prompt-registry`, `S06.system-prompt-model-specific`, `S06.system-prompt-custom`, `S06.persona-soul`).** Scripted one-turn conversations for each way of choosing the prompt; the saved `SystemPromptEvent` is read back. Then a `SOUL.md` is written into the run's `OH_PERSISTENCE_DIR` and a new program picks it up.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S06/prompt_variants \
    --check default_tags contains SOUL --check default_tags contains SECURITY --check default_tags contains MEMORY \
    --check static_same_across_contexts eq true --check static_has_dynamic_values eq false \
    --check dynamic_has_values eq true --check soul_default eq true \
    --check planning_differs eq true --check planning_tags contains PLAN_STRUCTURE \
    --check plain_gpt5_section eq false --check gpt5_gpt5_section eq true --check gpt5_preamble eq true \
    --check codex_gpt5_section eq true --check codex_preamble eq false \
    --check canonical_gpt5_section eq true --check override_preamble eq true \
    --check claude_vendor eq '["Claude-Specific"]' --check gemini_vendor eq '["Gemini-Specific"]' \
    --check gpt4o_vendor len-eq 0 --check gpt4o_baseline eq true \
    --check inline_starts eq true --check inline_has_default_tags eq false \
    --check template_rendered eq "Team blue prompt for test-model." \
    --check missing_template_error eq FileNotFoundError --check missing_relative_error eq FileNotFoundError \
    --check inline_and_file_error eq ValidationError \
    --check persona_starts eq true --check persona_tags not-contains SOUL --check persona_tags not-contains ROLE \
    --check persona_tags contains SECURITY --check persona_tags contains MEMORY
  control-sdk exec -- sh -c 'printf "%s\n" "You are Bob, a terse build engineer." > "$OH_PERSISTENCE_DIR/SOUL.md"'
  control-sdk program S06/prompt_variants --check soul_custom eq true --check soul_default eq false
  ```
- **Persistent memory (`S06.memory-load`, `S06.memory-write-path`).** User and project `MEMORY.md` indexes (and a daily log) are written to disk; conversations with `AgentContext(load_memory=True)` start over them, then over an oversized user index, then over an empty project index and an unreadable user index.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S06/memory \
    --check memory_before_first_message missing --check user_in_prompt eq true \
    --check project_in_prompt eq true --check user_before_project eq true \
    --check daily_log_in_prompt eq false --check memory_in_static eq false \
    --check write_path_advertised eq true --check memory_in_agent_context_dump eq false \
    --check memory_in_persisted_state eq false \
    --check big_first_line_kept eq false --check big_last_line_kept eq true \
    --check big_truncation_notice eq true --check big_partial_lines eq 0 --check big_kept_lines gt 50 \
    --check broken_status eq finished --check broken_has_memory_block eq false
  ```
  The `<MEMORY>` guidance names `$OH_PERSISTENCE_DIR/memory/`, the same directory the loader read.
- **Size-triggered summaries and the view (`S06.summarizing-size`, `S06.summary-llm-streaming`, `S06.view`, `S06.view-atomicity`).** A scripted agent runs ten terminal steps (one a parallel batch of two calls) under `LLMSummarizingCondenser(max_size=10, keep_first=2)` whose own scripted LLM has `stream=True` and an extra body.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S06/condense --arg size \
    --check status eq finished --check condensations ge 2 \
    --check first_summary eq "SUMMARY-1: echoed steps 0-3." --check first_offset eq 2 \
    --check kept_first_two eq true --check atomic eq true \
    --check view_kinds_head.2 eq CondensationSummaryEvent --check view_summaries eq 1 \
    --check forgotten_in_view eq false --check view_has_request eq false \
    --check summary_reached_agent eq true --check second_builds_on_first eq true \
    --check summary_usage_ids eq '["condenser"]' --check summary_call_streamed eq false \
    --check summary_call_kwargs not-contains extra_body --check summary_roles eq '["system", "user"]'
  ```
- **Token-triggered summaries (`S06.summarizing-tokens`).** `max_tokens` is three times the system prompt's token count; each command prints about 3 KB.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S06/condense --arg tokens \
    --check status eq finished --check condensations ge 1 --check events_total lt 200 \
    --check view_under_max_tokens eq true --check atomic eq true --check summary_reached_agent eq true
  ```
- **Reasons, minimum progress and the no-op condenser (`S06.summarizing-reasons`, `S06.view`, `S06.noop-condenser`).** The `View` of a finished 12-step conversation, with and without a `CondensationRequest`, is handed to condensers configured for each reason, as a custom condenser author would.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S06/condense --arg reasons \
    --check request_view_has_request eq false --check request_view_unhandled eq true \
    --check request_reasons eq '["request"]' --check events_reasons eq '["events"]' \
    --check request_events_tokens_reasons eq '["events", "request", "tokens"]' \
    --check most_aggressive_wins eq true \
    --check minimum_progress_result eq View --check minimum_progress_summary_calls eq 0 \
    --check keep_first_too_big_error eq ValidationError --check noop_same_view eq true
  ```
  Each reason forgets more: the request alone halves the view, the size reason forgets down to `max_size // 2`, and adding a token cap forgets the most; the combination takes the most aggressive.
- **Soft versus hard requirements (`S06.soft-vs-hard`).** With `minimum_progress=0.95` nothing is worth forgetting: size pressure leaves the run alone (no summary call), while `conversation.condense()` (a hard request) resets the context.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S06/condense --arg soft \
    --check status eq finished --check events gt 10 --check condensations_soft eq 0 \
    --check summary_calls_soft eq 0 --check condensations_after_request eq 1 \
    --check view_kinds_after_request eq '["SystemPromptEvent", "CondensationSummaryEvent"]' --check hard_offset eq 1
  ```
- **Hard context reset (`S06.hard-reset`).** The summary LLM fails on the normal path and on the first reset attempt, then answers; with every attempt failing, `condense()` raises and nothing is forgotten.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S06/condense --arg hard \
    --check summary_calls eq 3 --check retry_shrank eq true --check condensations eq 1 \
    --check summary eq "SUMMARY-H: reset." --check offset eq 1 --check system_prompt_kept eq true \
    --check view_kinds eq '["SystemPromptEvent", "CondensationSummaryEvent"]' \
    --check exhausted_error eq NoCondensationAvailableException --check exhausted_calls eq 3 \
    --check exhausted_condensations eq 0
  ```
  The third summary prompt is shorter than the second: the retry capped each event at 0.8 of the longest one.
- **Manual condense (`S06.manual-condense`).** `conversation.condense()` after a finished run, then another turn; and the `ValueError` without a condenser or with `NoOpCondenser`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S06/condense --arg manual \
    --check request_events eq 1 --check condensations eq 1 --check view_after lt 12 \
    --check request_in_view eq false --check summary_calls eq 1 --check agent_calls_during_condense eq 0 \
    --check error_after missing --check summary_reached_agent eq true \
    --check none_error contains "No condenser configured" --check none_error contains LLMSummarizingCondenser \
    --check noop_error contains "does not handle condensation requests"
  ```
- **Context-window errors (`S06.context-overflow-request`).** The agent's scripted LLM raises `LLMContextWindowExceedError` mid-task.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S06/condense --arg overflow \
    --check with_error missing --check with_status eq finished --check with_requests eq 1 \
    --check with_condensations eq 1 --check summary_reached_agent eq true \
    --check without_status eq error --check without_requests eq 0 \
    --check without_error_events eq '["LLMContextWindowExceedError"]'
  ```
- **Resume a condensed conversation (`S06.condenser-resume`).** The conversation is closed after two condensations, reopened from its persisted events by id, and sent another message.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S06/condense --arg resume \
    --check condensations ge 1 --check reopened_view_matches eq true --check rebuilt_view_matches eq true \
    --check reopened_summaries eq 1 --check error_after_resume missing \
    --check resumed_call_has_summary eq true --check resumed_status eq finished
  ```
- **Pipeline and no-op condensers in a conversation (`S06.pipeline-condenser`, `S06.noop-condenser`).** `PipelineCondenser([NoOpCondenser(), summarizer, second summarizer])` and a bare `NoOpCondenser` over the same long run.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S06/condense --arg pipeline \
    --check pipeline_handles_requests eq true --check noop_only_pipeline_handles_requests eq false \
    --check pipeline_status eq finished --check pipeline_condensations ge 1 --check pipeline_atomic eq true \
    --check pipeline_second_member_calls eq 0 --check pipeline_condensations_after_request gt 1 \
    --check noop_status eq finished --check noop_condensations eq 0 --check noop_view_len eq 20
  ```
  The second summarizer is never called: the pipeline stops at the first `Condensation`.
- **Summaries from a real model (`S06.summarizing-size`, `S06.manual-condense`, `S06.view-atomicity`).** Live: the agent and the summary LLM are the run's model.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk program S06/condense --arg live \
    --check status eq finished --check condensations ge 1 --check summary_nonempty eq true \
    --check atomic eq true --check condensations_after_request gt 1
  ```
- **Example 14 (`S06.summarizing-size`, `S06.condenser-resume`, `S06.summary-llm-streaming`).** Live.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output "Conversation finished with LLM Summarizing Condenser." -- python examples/01_standalone_sdk/14_context_condenser.py
  ```
  The example runs several turns, serializes and reloads the conversation, and keeps going after the reload.
- **Example 55 (`S06.memory-load`, `S06.memory-write-path`).** Live: session 1 asks the agent to record a decision; session 2 starts over the same workspace.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output "injected into session 2's system prompt: True" -- python examples/01_standalone_sdk/55_persistent_memory.py
  ```
- **Example 3 (`S06.context-suffixes`, `S06.context-skills`).** Live; also clones the public skills (needs network).
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output "EXAMPLE_COST" -- python examples/01_standalone_sdk/03_activate_skill.py
  ```
- **Condenser integration tests (`S06.summarizing-size`, `S06.summarizing-tokens`, `S06.soft-vs-hard`, `S06.hard-reset`, `S06.context-overflow-request`).** Live, through the integration runner (c02 to c05).
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env -- python tests/integration/run_infer.py --llm-config '{"model": "deepseek/deepseek-flash"}' --eval-ids c02_hard_context_reset,c03_delayed_condensation,c04_token_condenser,c05_size_condenser
  ```
- **Thinking blocks across condensation (`S06.view-atomicity`), blocked.** `c01_thinking_block_condenser` needs an Anthropic model with extended thinking (and its key); with other models it reports a skip.

## Gotchas

- `LLMSummarizingCondenser` raises at construction when `keep_first >= max_size // 2 - 1` leaves no tail; small `max_size` values in experiments hit this.
- Give the condenser LLM its own `usage_id` (`llm.model_copy(update={"usage_id": "condenser"})`), otherwise its cost is merged into the agent's in `conversation_stats`.
- A size trigger (soft requirement) that finds nothing safe to forget silently does nothing; do not expect a `Condensation` exactly at `max_size` events (see c03).
- Example 14 writes `./.conversations` in the current directory and resumes from it.
- Example 55 runs in a temporary workspace, but the user tier is read from `~/.openhands/memory/MEMORY.md` (or `$OH_PERSISTENCE_DIR/memory/`); an existing file there changes what session 1 sees. Set `OH_PERSISTENCE_DIR` to a temp dir for a clean run.
- `~/.openhands/SOUL.md` changes the identity line of every built-in prompt; prompt snapshot tests guard against that, but ad-hoc prompt comparisons do not. Regenerate snapshots with `REGEN_PROMPT_SNAPSHOTS=1 uv run pytest tests/sdk/context/prompts/test_prompt_snapshot.py`.
- `memory_context` is resolved at the first `send_message()`/`run()` (the workspace is not known when `AgentContext` validates), so inspecting `agent.agent_context` before that shows no memory.
- `c01_thinking_block_condenser.py` skips models without `extended_thinking`; models with `reasoning_effort` (OpenAI o-series, GPT-5) produce reasoning items, not thinking blocks.
- No failing tests were found in this family's commands at 4cc0582.
