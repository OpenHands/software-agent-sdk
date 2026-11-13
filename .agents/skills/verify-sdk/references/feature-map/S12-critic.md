# Critic and critic-driven refinement

A Python program passes `Agent(critic=...)` and the agent scores its own work while it runs: in the default `finish_and_message` mode the critic evaluates the event history whenever the agent emits a `FinishAction` or a plain agent message, and in `all_actions` mode after every action. The result, a `CriticResult` with a 0 to 1 `score`, a `message` and optional `metadata`, is attached to the emitted `ActionEvent` or `MessageEvent` as `critic_result` and rendered by the visualizer as stars and categorized features. With `critic.iterative_refinement=IterativeRefinementConfig(success_threshold, max_iterations)`, a `FinishAction` whose critic result is below the threshold (or, for `APIBasedCritic`, shows a high-probability agent issue) does not finish the conversation: `Conversation.run()` injects a user follow-up prompt and keeps going, counting refinements in `state.agent_state["iterative_refinement_iteration"]` until the score passes or `max_iterations` is reached. Built-in critics: `APIBasedCritic` (a remote classifier service), `AgentFinishedCritic`, `EmptyPatchCritic` and `PassCritic` (deterministic). `OpenHandsAgentSettings.verification` builds an `APIBasedCritic` from settings.

Source: `openhands-sdk/openhands/sdk/critic/`, `openhands-sdk/openhands/sdk/agent/critic_mixin.py`, `openhands-sdk/openhands/sdk/agent/agent.py`, `openhands-sdk/openhands/sdk/agent/response_dispatch.py`, `openhands-sdk/openhands/sdk/event/llm_convertible/action.py`, `openhands-sdk/openhands/sdk/event/llm_convertible/message.py`, `openhands-sdk/openhands/sdk/settings/model.py`

Needs: `llm`, `network`

Examples: `examples/01_standalone_sdk/34_critic_example.py`

## Sub-features

- `S12.critic-result`: `CriticResult(score, message, metadata)` validates `score` in [0, 1]; `success` is `score >= 0.5`. Unit: `tests/sdk/critic/test_critic.py::test_critic_result_success_threshold`, `::test_critic_result_validation`. Example: none. Real-LLM: none.
- `S12.critic-display`: `CriticResult.visualize` renders a star rating with colors, the JSON probability message sorted, and categorized features above the display threshold. Unit: `tests/sdk/critic/test_critic_display.py`. Example: `examples/01_standalone_sdk/34_critic_example.py`. Real-LLM: none.
- `S12.rule-critics`: `PassCritic` always scores 1.0; `EmptyPatchCritic` scores 0.0 for an empty or missing `git_patch`; `AgentFinishedCritic` needs both a non-empty patch and a last `ActionEvent` that is a `FinishAction`; `CriticBase` is abstract. Unit: `tests/sdk/critic/test_critic.py`. Example: none. Real-LLM: none.
- `S12.eval-mode`: `mode="finish_and_message"` evaluates on `FinishAction` (and on agent `MessageEvent`s in the response path), `mode="all_actions"` on every action, no critic on nothing. Unit: `tests/sdk/agent/test_iterative_refinement.py::TestShouldEvaluateWithCritic`. Example: none. Real-LLM: none.
- `S12.result-on-events`: during a run the critic result lands on the emitted `ActionEvent.critic_result` / `MessageEvent.critic_result`; a critic exception is logged and the event is emitted without a result. Unit: **none (untested)** (only the decision function above is tested; no test runs `Agent.step` with a critic). Example: `examples/01_standalone_sdk/34_critic_example.py`. Real-LLM: none.
- `S12.refinement-config`: `IterativeRefinementConfig` defaults to `success_threshold=0.6`, `max_iterations=3`, and rejects a threshold outside [0, 1] or `max_iterations < 1`. Unit: `tests/sdk/agent/test_iterative_refinement.py::TestIterativeRefinementConfig`. Example: none. Real-LLM: none.
- `S12.refinement-decision`: after a `FinishAction` the agent continues only when a critic and refinement config exist, the finish carries a critic result, `should_refine` is true and the counter is below `max_iterations`; a score at the threshold stops; the counter in `agent_state` increments only when refinement continues. Unit: `tests/sdk/agent/test_iterative_refinement.py::TestCheckIterativeRefinement`. Example: `examples/01_standalone_sdk/34_critic_example.py`. Real-LLM: none.
- `S12.refinement-followup`: when refinement continues, the action batch emits a `MessageEvent(source="user")` with the critic's follow-up prompt instead of marking the conversation FINISHED; a finish blocked by a hook is not finalized. Unit: `tests/sdk/agent/test_action_batch.py::test_finalize_emits_followup_on_refinement`, `::test_finalize_marks_finished`, `::test_finalize_noop_when_finish_blocked`. Example: `examples/01_standalone_sdk/34_critic_example.py`. Real-LLM: none.
- `S12.custom-refine-policy`: subclasses override `should_refine` and `get_followup_prompt`; `APIBasedCritic.should_refine` also refines when an agent behavioral issue has probability at or above `issue_threshold` (0.75), even if the score meets the threshold. Unit: `tests/sdk/agent/test_iterative_refinement.py::TestCheckIterativeRefinement::test_high_probability_issue_continues_even_when_score_meets_threshold`. Example: `examples/01_standalone_sdk/34_critic_example.py` (`SingleRefinementCritic`). Real-LLM: none.
- `S12.api-followup-prompt`: `APIBasedCritic.get_followup_prompt` lists agent issues, predicted user follow-ups, infrastructure issues and other observations with probabilities. Unit: **none (untested)**. Example: `examples/01_standalone_sdk/34_critic_example.py`. Real-LLM: none.
- `S12.api-evaluate`: `APIBasedCritic.evaluate` requires a `SystemPromptEvent` with tools, condenses the history through `View`, renders it with the Qwen chat template, POSTs `{server_url}/classify` with a bearer key (retrying HTTP 500 up to 3 times), and returns `score = probs["success"]` with `event_ids` and `categorized_features` metadata. Unit: **none (untested)** (no test mocks `/classify`). Example: `examples/01_standalone_sdk/34_critic_example.py`. Real-LLM: none.
- `S12.chat-template`: `ChatTemplateRenderer` reproduces the `transformers` rendering of the Qwen3 template, tools included. Unit: `tests/sdk/critic/api/test_template_render.py`. Example: none. Real-LLM: none.
- `S12.api-key-handling`: `APIBasedCritic.api_key` accepts `str` or `SecretStr`, treats empty and whitespace keys and the redacted placeholder as missing, refuses inference without a key, hides the key in `repr` and dumps, and exposes or encrypts it only with an expose or cipher context, also when nested in a dumped `Agent`. Unit: `tests/sdk/critic/test_critic_client.py`. Example: none. Real-LLM: none.
- `S12.settings-critic`: `OpenHandsAgentSettings.verification` (`critic_enabled`, `critic_mode`, `enable_iterative_refinement`, `critic_threshold`, `max_refinement_iterations`, `critic_server_url`, `critic_model_name`, `critic_api_key`) builds an `APIBasedCritic` in `create_agent()`, falls back to the LLM key, and builds none without any key. Unit: `tests/sdk/test_settings.py` (`test_llm_create_agent_*critic*`, `test_verification_settings_critic_api_key_roundtrip`). Example: none. Real-LLM: none.
- `S12.end-to-end-refinement`: a live run with a critic below threshold performs exactly the configured number of refinements and still finishes. Unit: **none (untested)**. Example: `examples/01_standalone_sdk/34_critic_example.py`. Real-LLM: none (only via the example on the `test-examples` label).

## How to get to it (agent POV)

- `Agent(llm=..., tools=..., critic=APIBasedCritic(server_url=..., api_key=..., model_name=..., mode=..., iterative_refinement=IterativeRefinementConfig(...)))`; imports from `openhands.sdk.critic`. Covers `S12.eval-mode`, `S12.result-on-events`, `S12.refinement-*`, `S12.api-*`.
- `PassCritic()`, `EmptyPatchCritic()`, `AgentFinishedCritic()`: call `.evaluate(events, git_patch=...)` directly; they are useful offline or in evaluation harnesses (`S12.rule-critics`).
- Subclass `CriticBase` (implement `evaluate`) or `APIBasedCritic` (override `should_refine` / `get_followup_prompt`) (`S12.custom-refine-policy`).
- Read the outcome: `event.critic_result` on `ActionEvent`/`MessageEvent` in `conversation.state.events`; `conversation.state.agent_state[ITERATIVE_REFINEMENT_ITERATION_KEY]` (`openhands.sdk.agent.critic_mixin`).
- `OpenHandsAgentSettings(llm=..., verification=VerificationSettings(critic_enabled=True, ...)).create_agent()` (`S12.settings-critic`).
- Defaults: `server_url="https://llm-proxy.app.all-hands.dev/vllm"`, `model_name="critic"`, `tokenizer_name="Qwen/Qwen3-4B-Instruct-2507"`, `timeout_seconds=300`.

## Driving it

Preconditions:
- `uv sync --dev` has run; `control-sdk` is on `PATH` (`export PATH="$PWD/.agents/skills/verify-sdk/scripts:$PATH"`). `sdk-map run` does both.
- `tmux` on `PATH` (the conversations use `TerminalTool`).
- No critic service is needed: `programs/S12/refinement_run.py` serves a stand-in `/classify` endpoint on `127.0.0.1` and seeds the tokenizer-config cache from `tests/fixtures/tokenizers/qwen3-4b-instruct-2507-tokenizer_config.json`, so `APIBasedCritic` runs its real request path offline.
- Live bullets: `DEEPSEEK_API_KEY` exported.

- **Critic objects (`S12.critic-result`, `S12.critic-display`, `S12.rule-critics`, `S12.refinement-config`, `S12.custom-refine-policy`, `S12.api-followup-prompt`, `S12.api-key-handling`, `S12.chat-template`, `S12.settings-critic`).** Results and their rendering (features grouped by `categorize_features` as `APIBasedCritic.evaluate` does), the rule critics over the events of two scripted conversations (one finishes, one ends with a message), `APIBasedCritic`'s refine policy and follow-up prompt, key handling also nested in a dumped `Agent`, the Qwen3 template, and critics built by `OpenHandsAgentSettings.create_agent()`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S12/critic_objects \
    --check success_at_half eq true --check success_below_half eq false \
    --check score_above_one_rejected eq true --check score_below_zero_rejected eq true \
    --check display_scored contains "★★★☆☆" --check display_scored contains 62.0% \
    --check display_scored matches "Potential Issues: Insufficient Testing .likelihood 80%. · Loop Behavior" \
    --check display_scored not-contains "Scope Creep" --check display_scored contains "Likely Follow-up: Correction" \
    --check display_plain contains "★☆☆☆☆" --check display_plain contains loop_behavior \
    --check pass eq 1.0 --check empty_patch_none eq 0.0 --check empty_patch_blank eq 0.0 --check empty_patch_with_patch eq 1.0 \
    --check finished_with_patch eq 1.0 --check finished_no_patch eq 0.0 --check unfinished_with_patch eq 0.0 \
    --check critic_base_abstract eq true \
    --check config_defaults.success_threshold eq 0.6 --check config_defaults.max_iterations eq 3 \
    --check threshold_above_one_rejected eq true --check threshold_below_zero_rejected eq true --check zero_iterations_rejected eq true \
    --check api_refines_low_score eq true --check api_stops_at_threshold eq false \
    --check api_issue_below_threshold eq false --check api_issue_above_threshold eq true --check api_issue_threshold_default eq 0.75 \
    --check no_config_never_refines eq false --check subclass_refines eq true --check subclass_prompt eq "Round 2: try again." \
    --check api_followup contains "Potential agent issues: Insufficient Testing" \
    --check api_followup contains "Predicted user follow-up needs: Correction" \
    --check api_followup contains "Infrastructure issues: Infrastructure External Issue" \
    --check base_followup contains "iteration 3, predicted success likelihood: 20.0%" \
    --check key_str_is_secret eq true --check key_secretstr eq sk-b \
    --check key_empty_is_none eq true --check key_blank_is_none eq true --check key_redacted_is_none eq true \
    --check no_key_inference_error eq "api_key must be non-empty" \
    --check repr_hides_key eq true --check dump_hides_key eq true --check expose_shows_key eq sk-x \
    --check agent_dump_hides_key eq true --check agent_expose_shows_key eq true \
    --check agent_cipher_hides_key eq true --check agent_cipher_restores_key eq sk-nested \
    --check template_user_exact eq true --check template_system_first eq true --check template_tools eq true --check template_generation_prompt eq true \
    --check settings_critic eq APIBasedCritic --check settings_mode eq all_actions \
    --check settings_threshold eq 0.7 --check settings_max_iterations eq 4 \
    --check settings_server_url eq http://127.0.0.1:9/vllm --check settings_model_name eq my-critic \
    --check settings_key eq critic-key --check fallback_key eq llm-key --check fallback_refinement missing \
    --check no_key_critic missing --check disabled_critic missing
  ```
- **Critics in a running conversation (`S12.eval-mode`, `S12.result-on-events`, `S12.refinement-decision`, `S12.refinement-followup`).** A program-defined `CriticBase` subclass hands out fixed scores to a scripted agent: refinement up to `max_iterations`, a score exactly at the threshold, an agent that ends with a plain message, `all_actions` mode, a critic that raises, and a `PreToolUse` hook that blocks the first finish.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S12/refinement_run \
    --check refine.status eq finished --check refine.refinements eq 2 --check refine.finish_scores len-eq 3 \
    --check refine.followups len-eq 2 --check refine.followups.0 contains "iteration 1, predicted success likelihood: 20.0%" \
    --check refine.followups.1 contains "iteration 2" --check refine.terminal_scores.0 missing \
    --check threshold.status eq finished --check threshold.refinements eq 1 --check threshold.finish_scores.1 eq 0.6 \
    --check threshold.followups len-eq 1 \
    --check message.status eq finished --check message.message_scores.0 eq 0.1 \
    --check message.followups len-eq 0 --check message.refinements eq 0 \
    --check all.terminal_scores.0 eq 0.9 --check all.finish_scores.0 eq 0.8 \
    --check broken.status eq finished --check broken.finish_scores.0 missing --check broken.followups len-eq 0 \
    --check hooked.status eq finished --check hooked.rejections.0 eq hook --check hooked.finish_scores len-eq 2 \
    --check hooked.followups len-eq 0 --check hooked.refinements eq 0
  ```
  Each refinement is a user `MessageEvent` with the critic's follow-up prompt instead of a FINISHED status; the counter in `agent_state` stops at 2; a plain agent message gets a `critic_result` but no follow-up; a raising critic leaves `critic_result` empty and the run finishes; the hook-blocked finish (scored 0.2) is not finalized and triggers no follow-up.
- **APIBasedCritic end to end, against a stand-in service (`S12.api-evaluate`, `S12.api-followup-prompt`, `S12.settings-critic`, `S12.end-to-end-refinement`, `S12.critic-display`).** `OpenHandsAgentSettings(verification=VerificationSettings(critic_enabled=True, enable_iterative_refinement=True, critic_server_url=<stand-in>, ...)).create_agent()` builds the critic; the stand-in answers HTTP 500 once, then success 0.3 with `loop_behavior` 0.8, then 0.95.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S12/refinement_run \
    --check api.status eq finished --check api.refinements eq 1 \
    --check api.needs_system_prompt contains "SystemPromptEvent is required" \
    --check api.requests eq 3 --check api.paths.0 eq /vllm/classify --check api.auth.0 eq "Bearer critic-key" \
    --check api.models.0 eq my-critic --check api.input_starts_with_system eq true --check api.input_has_tools eq true \
    --check api.first_score eq 0.3 --check api.first_event_ids gt 0 --check api.first_issues.0 eq loop_behavior \
    --check api.first_display contains "Potential Issues: Loop Behavior" \
    --check api.last_score eq 0.95 \
    --check api.followups.0 matches "Potential agent issues: Loop Behavior .80%." \
    --check api.followups len-eq 1
  ```
  Three requests for two evaluations: the HTTP 500 is retried. The request body is the Qwen-rendered history (system first, tools included) and the bearer is the settings' `critic_api_key`.
- **The same with a real agent model (`S12.result-on-events`, `S12.end-to-end-refinement`).** Live; the critic is still the stand-in, so the refinement count is deterministic.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk program S12/refinement_run --arg live \
    --check api.status eq finished --check api.refinements eq 1 --check api.first_score eq 0.3 \
    --check api.greeting contains hello
  ```
- **Example 34 against the All-Hands critic (`S12.api-evaluate`, `S12.end-to-end-refinement`), blocked.** Needs a reachable critic service: an All-Hands proxy `LLM_BASE_URL` (`llm-proxy.<env>.all-hands.dev`) or `CRITIC_SERVER_URL`, `CRITIC_API_KEY` and `CRITIC_MODEL_NAME`, plus network access to `huggingface.co` for the tokenizer config. With those, `control-sdk exec --model-env --env CRITIC_SERVER_URL=... -- python examples/01_standalone_sdk/34_critic_example.py` should print `Completed 1 critic-driven refinement.`.

## Gotchas

- Example 34 raises `ValueError` at start if neither the All-Hands proxy base URL nor all three `CRITIC_*` env vars are set. Its `SingleRefinementCritic` forces `should_refine=True`, so the refinement count is deterministic regardless of score.
- `APIBasedCritic` downloads `tokenizer_config.json` for `tokenizer_name` from `huggingface.co` on first use and caches it in `~/.cache/chat_templates/`; without network and without that cache, evaluation fails, the error is logged and `critic_result` stays `None` (the run continues, so refinement silently never triggers).
- Without `iterative_refinement` the critic only annotates events; it never changes control flow.
- Refinement is checked only for a `FinishAction`; an agent that ends with a plain message gets a `critic_result` on the `MessageEvent` but no follow-up.
- `CriticResult.success` uses a fixed 0.5 threshold, independent of `IterativeRefinementConfig.success_threshold` (default 0.6).
- `mode="all_actions"` makes one HTTP call per action and is much slower.
- Example 31 (`31_iterative_refinement.py`) is a user-written refinement loop with a second LLM, not the critic; example 54 (goal completion loop) is a separate mechanism in `conversation/goal/`. Neither belongs here.
- `34_critic_example.py` is not in `_EXCLUDED_EXAMPLES`, so it runs on the `test-examples` label and needs the critic credentials there.
- `huggingface.co` is unreachable from this container (proxy 403); seed `~/.cache/chat_templates/<md5(tokenizer_name)>_tokenizer_config.json` (the repo has a fixture copy) to run `APIBasedCritic` offline. The cache path is computed from `Path.home()` at import time.
- The critic is evaluated on a `FinishAction` before `PreToolUse` hooks decide whether it runs; a hook-blocked finish still carries a `critic_result` but never triggers a follow-up.
- `categorize_features` (called by `APIBasedCritic.evaluate`) applies the 0.2 display threshold and the sort; `CriticResult.visualize` renders whatever `categorized_features` it is given.
