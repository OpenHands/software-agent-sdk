# LLM registry, routing, fallback, profiles and model switching

A Python program that uses more than one model meets this family. `LLMRegistry` holds `LLM`s by `usage_id`, gives each one independent metrics, and notifies subscribers; every `LocalConversation` has its own `conversation.llm_registry`, into which it registers every LLM the agent reaches (the agent's, the condenser's, router children), and `conversation_stats` tracks them all. A `RouterLLM` (`MultimodalRouter`, `RandomRouter`) is itself an `LLM` that picks one of `llms_for_routing` per call; `MultimodalRouter` sends a request with images, or one too large for the secondary model, to `primary` and everything else to `secondary`. `LLM(fallback_strategy=FallbackStrategy(fallback_llms=[...]))` retries a call that failed with a transient error on saved profiles, in order, and merges their cost into the primary's metrics. `LLMProfileStore` saves and loads named `LLM` configurations as JSON (default `~/.openhands/profiles`, secrets masked unless asked for). A running conversation can change model with `conversation.switch_profile(name)` or `conversation.switch_llm(llm)`, the agent can do it itself with the `switch_llm` tool, or a classifier LLM can pick a profile through the `route_task_to_model` tool configured by a meta-profile. `LLM.subscription_login(vendor="openai", model=...)` returns an `LLM` backed by a ChatGPT Plus/Pro subscription through OAuth instead of an API key.

Source: `openhands-sdk/openhands/sdk/llm/llm_registry.py`, `openhands-sdk/openhands/sdk/llm/router/`, `openhands-sdk/openhands/sdk/llm/fallback_strategy.py`, `openhands-sdk/openhands/sdk/llm/llm_profile_store.py`, `openhands-sdk/openhands/sdk/llm/meta_profile_store.py`, `openhands-sdk/openhands/sdk/llm/provider_connection_store.py`, `openhands-sdk/openhands/sdk/llm/auth/`, `openhands-sdk/openhands/sdk/llm/llm.py`, `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`, `openhands-sdk/openhands/sdk/conversation/conversation_stats.py`, `openhands-sdk/openhands/sdk/tool/builtins/switch_llm.py`, `openhands-sdk/openhands/sdk/tool/builtins/classify_and_switch_llm.py`, `openhands-sdk/openhands/sdk/settings/model.py`

Needs: `llm`, `network`

Examples: `examples/01_standalone_sdk/05_use_llm_registry.py`, `examples/01_standalone_sdk/19_llm_routing.py`, `examples/01_standalone_sdk/35_subscription_login.py`, `examples/01_standalone_sdk/37_llm_profile_store/main.py`, `examples/01_standalone_sdk/39_llm_fallback.py`, `examples/01_standalone_sdk/44_model_switching_in_convo.py`, `examples/01_standalone_sdk/49_switch_llm_tool.py`, `examples/01_standalone_sdk/59_route_task_to_model.py`

## Sub-features

- `S03.registry-crud`: `LLMRegistry.add(llm)` keys by `usage_id` and refuses a duplicate, `get(usage_id)` returns the same instance or raises `KeyError`, `remove` and `list_usage_ids` work, and each registry has a unique id. Unit: `tests/sdk/llm/test_llm_registry.py`. Example: `examples/01_standalone_sdk/05_use_llm_registry.py`. Real-LLM: none.
- `S03.registry-independent-metrics`: an `LLM` copied from another and added to a registry gets its own `Metrics` and telemetry, so costs do not double-count; independent LLMs keep their metrics. Unit: `tests/sdk/llm/test_llm_registry.py::test_llm_registry_ensures_independent_metrics_for_copied_llms`, `::test_llm_registry_ensures_independent_telemetry_for_copied_llms`, `::test_llm_registry_does_not_reset_metrics_for_independent_llms`. Example: none. Real-LLM: none.
- `S03.registry-stats-wiring`: the conversation subscribes `conversation_stats` to its registry, so any LLM added later (e.g. `conversation.llm_registry.add(LLM(usage_id="x"))`) shows up in `usage_to_metrics`; a failing subscriber does not break `add`. Unit: `tests/sdk/conversation/test_conversation_stats.py::test_llm_registry_notifications`, `tests/sdk/llm/test_llm_registry.py::test_llm_registry_notify_exception_handling`. Example: `examples/01_standalone_sdk/21_generate_extraneous_conversation_costs.py`. Real-LLM: none.
- `S03.agent-llm-discovery`: the conversation finds every `LLM` field reachable from the agent (condenser, custom fields, router children), registers each `usage_id` once and dedupes duplicates. Unit: `tests/sdk/agent/test_agent_llms_are_discoverable.py`, `tests/sdk/conversation/test_switch_model.py::test_duplicate_usage_ids_in_registration_loop_are_silently_deduped`. Example: none. Real-LLM: none.
- `S03.multimodal-router-images`: `Agent(llm=MultimodalRouter(llms_for_routing={"primary": ..., "secondary": ...}))` sends a turn with an image to `primary`, and the non-vision guard does not pre-empt the router. Unit: `tests/sdk/agent/test_non_multimodal_image_input.py::test_image_input_guard_does_not_preempt_multimodal_router`. Example: `examples/01_standalone_sdk/19_llm_routing.py`. Real-LLM: none.
- `S03.multimodal-router-text`: text-only turns go to `secondary`, and turns whose token count exceeds the secondary's `effective_max_input_tokens` go to `primary`. Unit: **none (untested)**. Example: `examples/01_standalone_sdk/19_llm_routing.py` (text turns before and after the image turn). Real-LLM: none.
- `S03.router-validation`: `MultimodalRouter` without a `primary` or `secondary` key and any `RouterLLM` with empty `llms_for_routing` fail validation. Unit: **none (untested)**. Example: none. Real-LLM: none.
- `S03.random-router`: `RandomRouter` picks a random entry of `llms_for_routing` per call. Unit: **none (untested)**. Example: none. Real-LLM: none.
- `S03.fallback-transient`: after the primary exhausts its retries on a transient error (rate limit, timeout, connection, service unavailable, transient HTTP status) the fallback profiles are tried in order; the first success is returned; if all fail the primary's error is raised; each new call starts on the primary again. Chat and Responses, sync and async. Unit: `tests/sdk/llm/test_llm_fallback.py`. Example: `examples/01_standalone_sdk/39_llm_fallback.py`. Real-LLM: none.
- `S03.fallback-skip`: non-transient errors (bad request, auth, context window) and budget denials do not fall back; quota exhaustion fails over without retrying the primary. Unit: `tests/sdk/llm/test_llm_fallback.py::test_non_transient_error_skips_fallback`, `::test_budget_denial_stops_retries_and_fallbacks`, `::test_quota_exhaustion_fails_over_without_retries`. Example: none. Real-LLM: none.
- `S03.fallback-metrics`: cost and token usage of a fallback call are merged into the primary LLM's metrics; caller kwargs and the conversation call context are forwarded to the fallback. Unit: `tests/sdk/llm/test_llm_fallback.py::test_metrics_merged_from_fallback`, `::test_fallback_forwards_caller_kwargs`, `::test_completion_fallback_receives_call_context`. Example: `examples/01_standalone_sdk/39_llm_fallback.py`. Real-LLM: none.
- `S03.fallback-profile-resolution`: `fallback_llms` names are loaded from `LLMProfileStore(profile_store_dir)` lazily on first fallback. Unit: `tests/sdk/llm/test_llm_fallback.py::test_fallback_profiles_resolved_via_store`. Example: `examples/01_standalone_sdk/39_llm_fallback.py`. Real-LLM: none.
- `S03.profile-save-load`: `LLMProfileStore(base_dir).save(name, llm)` writes `<name>.json` atomically with a schema version, `load(name)` returns the `LLM`, `list()`, `list_summaries()`, `delete()`, `rename()` work; invalid names, missing and corrupted profiles raise with the available names listed. Unit: `tests/sdk/llm/test_llm_profile_store.py`. Example: `examples/01_standalone_sdk/37_llm_profile_store/main.py`. Real-LLM: none.
- `S03.profile-secrets`: `save` masks the API key unless `include_secrets=True`; with a `cipher` secrets are encrypted at rest and decrypted on `load`; `list_summaries` reports whether a key is set without exposing it. Unit: `tests/sdk/llm/test_llm_profile_store.py::test_save_without_secrets`, `::test_save_with_secrets`, `tests/sdk/conversation/test_switch_model.py::test_switch_profile_decrypts_with_cipher`. Example: `examples/01_standalone_sdk/37_llm_profile_store/main.py`. Real-LLM: none.
- `S03.profile-concurrency-limits`: concurrent saves and reads are serialized by a file lock, and `max_profiles` blocks new names over the limit but allows overwrites. Unit: `tests/sdk/llm/test_llm_profile_store.py::test_concurrent_saves`, `::test_concurrent_reads_and_writes`, `::test_save_with_max_profiles_blocks_over_limit`. Example: none. Real-LLM: none.
- `S03.profile-migration`: legacy OpenHands-proxy profiles are migrated on load, third-party LiteLLM-proxy profiles are kept, and a profile with a newer schema version is rejected. Unit: `tests/sdk/llm/test_llm_profile_store.py::test_load_migrates_legacy_openhands_proxy_profile`, `::test_load_rejects_newer_profile_schema_version`. Example: none. Real-LLM: none.
- `S03.provider-connections`: a profile can reference a saved provider connection (`provider_connection_id`) whose credentials are resolved at load time from a sibling `provider-connections` store. Unit: `tests/sdk/llm/test_provider_connection_store.py`, `tests/sdk/llm/test_llm_profile_store.py::test_default_provider_store_is_sibling_of_base_dir`. Example: none. Real-LLM: none.
- `S03.switch-profile`: `conversation.switch_profile(name)` loads the profile (cached in the registry as `profile:<name>`), swaps the agent's LLM, updates persisted state, keeps the prompt cache key, and raises for a missing profile; later messages use the new model and both models' costs appear in the stats. Unit: `tests/sdk/conversation/test_switch_model.py`. Example: `examples/01_standalone_sdk/44_model_switching_in_convo.py`. Real-LLM: none.
- `S03.switch-llm-object`: `conversation.switch_llm(llm)` swaps to an `LLM` object without touching the profile store, reuses a registry entry with the same `usage_id`, refreshes an LLM condenser that shared the agent's credentials while leaving an independently configured one alone, and `ask_agent` follows the switch. Unit: `tests/sdk/conversation/test_switch_model.py::test_switch_llm_swaps_when_store_empty`, `::test_switch_llm_refreshes_llm_condenser_credentials`, `::test_switch_llm_preserves_independent_condenser_profile`, `tests/sdk/conversation/test_ask_agent.py::test_ask_agent_llm_refreshes_after_switch_llm`. Example: none. Real-LLM: none.
- `S03.switch-llm-tool`: with `SwitchLLMTool` among the agent's tools (`include_default_tools=["FinishTool", "SwitchLLMTool"]`, or `OpenHandsAgentSettings.create_agent()`, which adds it when `tools` is left unset), the model calls `switch_llm(profile_name=...)`; the tool description lists saved profiles, the conversation switches, and a missing profile or load error comes back as an error observation. The tool is exempt from `filter_tools_regex` and does not deadlock under `arun()`. Unit: `tests/sdk/tool/test_switch_llm.py`, `tests/sdk/conversation/test_switch_model.py::test_switch_llm_tool_during_arun_does_not_deadlock`. Example: `examples/01_standalone_sdk/49_switch_llm_tool.py`. Real-LLM: none.
- `S03.route-task-to-model`: with `OpenHandsAgentSettings(enable_classify_and_switch_llm_tool=True, meta_profile=..., active_meta_profile=...)` the agent gets `route_task_to_model`; calling it sends recent conversation text to the classifier profile, which returns a class index (structured `classes`) or a model name (`prompt_template` with `{{ instance_text }}` and `{{ model_table }}`), and the conversation switches to that profile. Classifier cost lands in the conversation stats under one usage bucket; unknown classes, missing classifier or target profiles produce error observations. Unit: `tests/sdk/tool/test_classify_and_switch_llm.py`. Example: `examples/01_standalone_sdk/59_route_task_to_model.py`. Real-LLM: none.
- `S03.meta-profile-store`: `MetaProfileStore` saves, lists, loads and deletes meta-profiles under `OH_PERSISTENCE_DIR` (or `~/.openhands`), validates direct-prompt templates (requires `{{ instance_text }}`, no `classes`), and enforces `max_profiles`. Unit: `tests/sdk/llm/test_meta_profile_store.py`. Example: none. Real-LLM: none.
- `S03.subscription-login`: `LLM.subscription_login(vendor="openai", model=..., auth_method="browser"|"device_code", force_login=False, open_browser=True)` runs an OAuth PKCE browser flow or a device-code flow, caches credentials under `~/.openhands/auth/`, refreshes expired tokens, asks for consent once, and returns an `LLM` pointed at the Codex Responses endpoint; models outside the supported set are rejected. Unit: `tests/sdk/llm/auth/test_openai.py`, `tests/sdk/llm/auth/test_credentials.py`. Example: `examples/01_standalone_sdk/35_subscription_login.py`. Real-LLM: none.
- `S03.subscription-requests`: a subscription `LLM` omits parameters the Codex endpoint rejects or mishandles (`prompt_cache_retention`, `include`, `reasoning`, `temperature`, `max_output_tokens`), adds no temperature on retry, keeps `is_subscription` across serialization, and rehydrates from `LLM.load_from_env`. Unit: `tests/sdk/llm/test_subscription_mode.py`, `tests/sdk/test_settings.py::test_llm_load_from_env_rehydrates_subscription_runtime`. Example: none. Real-LLM: none.

## How to get to it (agent POV)

- `LLMRegistry()` from `openhands.sdk`: `add`, `get`, `remove`, `list_usage_ids`, `subscribe`; `conversation.llm_registry` is the conversation's own instance. Covers `S03.registry-*`, `S03.agent-llm-discovery`.
- `MultimodalRouter(usage_id=..., llms_for_routing={"primary": llm_a, "secondary": llm_b})` from `openhands.sdk.llm.router`, passed as `Agent(llm=router)`; `RandomRouter` from `openhands.sdk.llm.router.impl.random`; subclass `RouterLLM` and implement `select_llm(messages) -> key` for a custom router. Covers `S03.*router*`.
- `LLM(..., fallback_strategy=FallbackStrategy(fallback_llms=["p1", "p2"], profile_store_dir=...))` with `FallbackStrategy` from `openhands.sdk.llm`. Covers `S03.fallback-*`.
- `LLMProfileStore(base_dir=None)` from `openhands.sdk`: `save(name, llm, include_secrets=False, cipher=None)`, `load(name, cipher=None)`, `list()`, `list_summaries()`, `delete()`, `rename()`; default directory `$OH_PERSISTENCE_DIR/profiles` or `~/.openhands/profiles`. Covers `S03.profile-*`, `S03.provider-connections`.
- `conversation.switch_profile(name)`, `conversation.switch_llm(llm)` on `LocalConversation`; `conversation.switch_acp_model(model)` is the ACP-agent counterpart (ACP family). Covers `S03.switch-profile`, `S03.switch-llm-object`.
- Tools: `Agent(include_default_tools=["FinishTool", "SwitchLLMTool"])`; `OpenHandsAgentSettings(tools=..., enable_classify_and_switch_llm_tool=True, active_meta_profile=..., meta_profile=MetaProfile(...), meta_profile_llms={...}).create_agent()` (the switch tool is included when `tools` is None or selects `switch_llm`; the old `enable_switch_llm_tool` is a read-only, deprecated property); `MetaProfile`, `MetaProfileClass`, `MetaProfileStore` from `openhands.sdk.llm.meta_profile_store`. Covers `S03.switch-llm-tool`, `S03.route-task-to-model`, `S03.meta-profile-store`.
- `LLM.subscription_login(...)`; files in `~/.openhands/auth/` (credentials, consent marker). Covers `S03.subscription-*`.
- Env vars: `OH_PERSISTENCE_DIR` (moves profiles, meta-profiles and auth; must be set before importing `openhands.sdk`), example-only `LLM_ROUTING_SECONDARY_MODEL`, `LLM_FALLBACK_MODEL_1/2`, `LLM_FALLBACK_API_KEY_1/2`, `LLM_FALLBACK_BASE_URL_1/2`, `OPENHANDS_SUBSCRIPTION_MODEL`, `OPENHANDS_SUBSCRIPTION_AUTH_METHOD`, `OPENHANDS_SUBSCRIPTION_FORCE_LOGIN`, `SUBSCRIPTION_LOGIN_ONLY`.

## Driving it

Preconditions:
- `uv sync --dev` has run; `control-sdk` is on `PATH` (`export PATH="$PWD/.agents/skills/verify-sdk/scripts:$PATH"`). `sdk-map run` does both.
- `tmux` on `PATH` (some conversations carry `TerminalTool`).
- Live bullets: `DEEPSEEK_API_KEY` exported.

Every model in the scripted bullets is a real `LLM` answering from its own local endpoint (`programs/S02/fake_provider.py`), so the programs can tell which model served each call and what it cost. Profiles go to the run's private `$OH_PERSISTENCE_DIR/profiles`, never the caller's `~/.openhands`.

- **The registry (`S03.registry-crud`, `S03.registry-independent-metrics`, `S03.registry-stats-wiring`).** Add, get, duplicate, remove; a copied LLM's metrics; a pre-spent LLM; a subscriber that raises.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S03/routing --arg registry \
    --check usage_ids.0 eq first --check usage_ids.1 eq copy --check same_instance eq true \
    --check duplicate_error eq ValueError --check unique_registry_ids eq true \
    --check first_cost eq 0.14 --check copy_cost eq 0 --check copy_metrics_independent eq true \
    --check notified.1 eq copy --check after_remove len-eq 1 --check missing_error eq KeyError \
    --check independent_keeps_metrics eq true \
    --check failing_subscriber_add_error missing --check failing_subscriber_registered.0 eq n
  ```
- **Stats follow the conversation's registry (`S03.registry-stats-wiring`).** A priced LLM added to `conversation.llm_registry` after the run (as example 21 does), and one added right after building the conversation, before the first message.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S02/conversation_stats \
    --check first_run.usage_ids contains secondary --check first_run.combined_cost eq 0.42
  control-sdk program S02/conversation_stats --arg before-first-message \
    --check first_run.secondary_in_registry eq true --check first_run.usage_ids contains secondary \
    --check first_run.secondary_cost eq 0.14 --check first_run.combined_cost eq 0.42
  ```
  Known failure (reproduced 2026-10-13, not yet filed): an LLM added to `conversation.llm_registry` before the first `send_message` never reaches `conversation_stats`. The conversation subscribes the stats to its registry only when it initializes the agent (on the first message), and `subscribe` does not replay LLMs already registered, so `secondary` sits in the registry while `usage_to_metrics` has only `agent`, and its 0.14 is missing from the combined cost (0.28).
- **Every LLM the agent reaches is registered (`S03.agent-llm-discovery`).** An agent whose LLM is a `MultimodalRouter` and whose condenser has its own LLM; then an agent and condenser that share a `usage_id`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S03/routing --arg discovery \
    --check status eq finished --check before_first_message len-eq 0 \
    --check registered contains router-primary --check registered contains router-secondary \
    --check registered contains condenser --check stats contains router-secondary \
    --check duplicate_usage_ids.error missing --check duplicate_usage_ids.registered len-eq 1
  ```
  Registration happens on the first message; the router's children and the condenser's LLM each get a slot (the router itself does not).
- **Multimodal routing in an agent run (`S03.multimodal-router-images`, `S03.multimodal-router-text`).** Primary `openai/gpt-4o-mini`, secondary `deepseek/deepseek-chat` (no vision, `max_input_tokens=20000`): a short text turn, an image turn, a 30000-word turn.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S03/routing --arg multimodal \
    --check served.0 eq secondary --check served.1 eq primary --check served.2 eq primary \
    --check secondary_vision eq false \
    --check agent_replies.0 eq "secondary: short answer" --check agent_replies.1 eq "primary: image answer" \
    --check agent_replies.2 eq "primary: long answer"
  ```
  The image turn reaches the primary model instead of the agent's "cannot see images" reply, so the non-vision guard does not pre-empt the router.
- **Router validation and `RandomRouter` (`S03.router-validation`, `S03.random-router`).**
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S03/routing --arg validation \
    --check errors.missing_secondary eq ValidationError --check errors.missing_primary eq ValidationError \
    --check errors.empty_random eq ValidationError --check errors.valid missing
  control-sdk program S03/routing --arg random --check calls eq 20 --check from_a gt 0 --check from_b gt 0
  ```
- **Fallback (`S03.fallback-transient`, `S03.fallback-skip`, `S03.fallback-metrics`, `S03.fallback-profile-resolution`).** A rate-limited primary with two fallback profiles (the first also rate-limited; the second saved after the primary was built), then a bad request, a budget denial, quota exhaustion, every model failing, the Responses path and `acompletion`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S03/routing --arg fallback \
    --check transient.text eq "from fallback 2" --check transient.fallback1_requests eq 1 \
    --check transient.fallback2_requests eq 1 --check transient.primary_cost eq 0.14 \
    --check next_call.text eq "primary again" --check next_call.primary_requests eq 2 \
    --check non_transient.error eq LLMBadRequestError --check non_transient.fallback_requests eq 0 \
    --check budget_denial.fallback_requests eq 0 \
    --check quota.text eq "from quota fallback" --check quota.primary_requests eq 1 \
    --check all_fail.error eq LLMRateLimitError --check all_fail.fallback_requests eq 1 \
    --check responses.text eq "responses fallback" --check responses.paths.0 eq /v1/responses \
    --check async.text eq "async fallback"
  ```
  The fallback's cost lands in the primary's metrics (0.14); quota exhaustion fails over on the first attempt although the primary allows three.
- **The profile store (`S03.profile-save-load`, `S03.profile-secrets`, `S03.profile-concurrency-limits`, `S03.profile-migration`).**
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S03/profiles --arg crud \
    --check files len-eq 2 --check tmp_leftovers eq 0 --check schema_version eq 1 \
    --check loaded_model eq openai/gpt-4o-mini --check loaded_temperature eq 0.1 --check summaries len-eq 2 \
    --check after_rename contains bold.json --check after_delete len-eq 1 \
    --check invalid_name contains "Invalid profile name" --check missing contains "Available profiles: fast" \
    --check corrupted contains "Failed to load profile"
  control-sdk program S03/profiles --arg secrets \
    --check masked_has_secret eq false --check masked_loaded_key missing \
    --check plain_has_secret eq true --check plain_loaded_key eq sk-profile-secret \
    --check encrypted_has_secret eq false --check encrypted_loaded_key eq sk-profile-secret \
    --check summaries.plain eq true --check summaries.masked eq false --check summaries_expose_secret eq false
  control-sdk program S03/profiles --arg concurrency \
    --check errors len-eq 0 --check reads_ok eq true --check profiles len-eq 5 --check tmp_leftovers eq 0 \
    --check over_limit_is_profile_limit eq true --check overwrite_error missing --check limited_profiles len-eq 2
  control-sdk program S03/profiles --arg migration \
    --check legacy_model eq openhands/claude-opus-5 --check legacy_base_url missing \
    --check thirdparty_model eq litellm_proxy/claude-opus-5 --check thirdparty_base_url eq https://litellm.example.com/ \
    --check future_error contains "newer than supported"
  ```
- **Provider connections (`S03.provider-connections`).** A profile that names a saved connection; the connection's key is rotated, then deleted.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S03/profiles --arg connections \
    --check stored_api_key missing --check stored_connection_id eq conn1 \
    --check loaded_key eq sk-shared-connection --check loaded_base_url eq https://anthropic.example.com \
    --check rotated_key eq sk-rotated --check dangling_error contains ProviderConnectionNotFound \
    --check sibling_dir_default eq true
  ```
- **Switching by profile (`S03.switch-profile`).** A persisted conversation on model A switches to the saved profile `model-b`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S03/switching --arg profile \
    --check requests.a eq 1 --check requests.b eq 1 --check active_model eq openai/gpt-4.1-mini \
    --check registry contains profile:model-b --check stats.agent eq 0.14 --check stats.profile:model-b eq 0.14 \
    --check persisted_model eq openai/gpt-4.1-mini --check missing_profile_error eq FileNotFoundError \
    --check model_after_missing eq openai/gpt-4.1-mini --check prompt_cache_key_kept eq true \
    --check prompt_cache_key_is_conversation_id eq true --check status eq finished
  ```
- **Switching to an LLM object (`S03.switch-llm-object`).** `switch_llm` with a new `usage_id`, `ask_agent` after it, a second object under the same `usage_id`, and two condenser setups.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S03/switching --arg llm-object \
    --check requests.a eq 1 --check requests.b eq 2 --check requests.c eq 0 \
    --check profile_store_untouched eq true --check side_answer eq "B answers the side question" \
    --check model_after_reswitch eq openai/gpt-4.1-mini \
    --check condenser_model eq openai/gpt-4.1-mini --check condenser_base_url_is_b eq true \
    --check independent_condenser_model eq anthropic/claude-sonnet-4-5-20250929
  ```
- **The `switch_llm` tool (`S03.switch-llm-tool`).** The model asks for a missing profile, then for `model-b`; `filter_tools_regex="^(finish|terminal)$"` is set; once with `run()` and once with `arun()`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S03/switching --arg tool \
    --check status eq finished --check active_model eq openai/gpt-4.1-mini \
    --check requests.a eq 2 --check requests.b eq 1 --check tools_sent contains switch_llm \
    --check description_lists_profile eq true \
    --check observations.0.error eq true --check observations.0.text contains "'missing-profile' was not found" \
    --check observations.1.error eq false --check observations.2.text eq "finished on B"
  control-sdk program S03/switching --arg tool-async \
    --check status eq finished --check active_model eq openai/gpt-4.1-mini --check requests.b eq 1
  ```
- **`route_task_to_model` (`S03.route-task-to-model`).** `OpenHandsAgentSettings(enable_classify_and_switch_llm_tool=True, meta_profile=...)`: structured classes (the classifier answers `2`), a direct prompt template (it answers `{"model": "cheap"}`), an answer out of range, and a missing classifier profile.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S03/switching --arg route \
    --check status eq finished --check active_model eq openai/gpt-4.1 \
    --check requests.classifier eq 1 --check requests.strong eq 1 --check requests.cheap eq 0 \
    --check classifier_saw_task eq true --check stats contains classifier:classifier \
    --check observations.0.text contains "switched to LLM profile 'strong'" \
    --check bad_class.error eq true --check bad_class.text contains "did not pick a valid category" \
    --check missing_classifier.error eq true --check missing_classifier.text contains "no-classifier"
  control-sdk program S03/switching --arg route-direct \
    --check status eq finished --check active_model eq openai/gpt-4.1-nano --check requests.cheap eq 1 \
    --check stats contains classifier:classifier --check bad_class.error eq true
  ```
- **Meta-profiles (`S03.meta-profile-store`).**
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S03/profiles --arg meta \
    --check list len-eq 2 --check loaded_classes.1 eq strong --check loaded_template eq true \
    --check after_delete len-eq 1 --check template_without_instance_text contains "instance_text" \
    --check template_and_classes contains "cannot also define classes" --check over_limit contains "limit reached"
  control-sdk program S03/profiles --arg default-dir \
    --check profile_dir matches '/openhands/profiles$' --check meta_profile_dir matches '/openhands/meta-profiles$'
  ```
- **Subscription access without a fresh login (`S03.subscription-login`, `S03.subscription-requests`).** Cached, unexpired credentials in `$OH_PERSISTENCE_DIR/auth`; the returned LLM's requests go to a local Responses endpoint.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S03/subscription \
    --check login_error missing --check is_subscription eq true --check model eq openai/gpt-5.5 \
    --check base_url eq https://chatgpt.com/backend-api/codex --check stream eq true \
    --check unsupported_model_error contains "not supported for subscription access" \
    --check forced_login_error contains "consent has not been previously acknowledged" \
    --check requests.text eq "codex reply" --check requests.count eq 2 \
    --check requests.omitted_everywhere len-eq 5 --check requests.store eq false \
    --check requests.authorization_uses_cached_token eq true --check requests.originator eq codex_cli_rs \
    --check serialized.is_subscription eq true --check serialized.token_in_dump eq false \
    --check from_env.is_subscription eq true --check from_env.model eq openai/gpt-5.5
  ```
  `temperature`, `max_output_tokens`, `include`, `reasoning` and `prompt_cache_retention` stay off every request, the retry after a 429 included.
- **Switching models live (`S03.switch-profile`, `S03.registry-stats-wiring`).** Live: DeepSeek flash, then the `deepseek-pro` profile.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk program S03/live_switch \
    --check first_model eq deepseek/deepseek-flash --check second_model eq deepseek/deepseek-v4-pro \
    --check status eq finished --check usage_ids contains profile:pro --check calls.agent ge 1 --check calls.profile:pro ge 1
  ```
- **Examples 05, 37 and 39 (`S03.registry-crud`, `S03.profile-save-load`, `S03.fallback-transient`).** Live (37 makes no model call but runs with the others).
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output "Same LLM instance: True" -- python examples/01_standalone_sdk/05_use_llm_registry.py
  control-sdk exec --model-env --expect-output "EXAMPLE_COST: 0" -- python examples/01_standalone_sdk/37_llm_profile_store/main.py
  control-sdk exec --model-env --env LLM_FALLBACK_MODEL_1=deepseek/deepseek-flash --env LLM_FALLBACK_MODEL_2=deepseek/deepseek-flash \
    --expect-output "including fallbacks" -- python examples/01_standalone_sdk/39_llm_fallback.py
  ```
- **Examples 19, 44, 49 and 59 (`S03.multimodal-router-images`, `S03.switch-profile`, `S03.switch-llm-tool`, `S03.route-task-to-model`), blocked.** Example 19 sends an image to `LLM_MODEL` as the primary and needs a vision model; 44 saves an `openhands/gpt-5.2` profile and needs an OpenHands provider key; 49 and 59 hardcode `openai/gpt-5.5` and `openai/prod/claude-sonnet-4-5-20250929` behind `https://llm-proxy.app.all-hands.dev` and need a key for that proxy. The run's DeepSeek key reaches none of them.
- **Interactive subscription login and example 35 (`S03.subscription-login`), blocked.** Needs a ChatGPT Plus/Pro account and an interactive browser or device-code login (`OPENHANDS_SUBSCRIPTION_MODEL=gpt-5.5 OPENHANDS_SUBSCRIPTION_AUTH_METHOD=device_code SUBSCRIPTION_LOGIN_ONLY=1 python examples/01_standalone_sdk/35_subscription_login.py`); token refresh needs the real OpenAI auth endpoint.

## Gotchas

- `35_subscription_login.py` is in `_EXCLUDED_EXAMPLES` and never runs on the `test-examples` label. Its default model `gpt-5.2-codex` is not in `OPENAI_CODEX_MODELS` (`gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5` in `openhands-sdk/openhands/sdk/llm/auth/openai.py`), so without `OPENHANDS_SUBSCRIPTION_MODEL` the example fails with an unsupported-model error after login. The `LLM.subscription_login` docstring lists yet another set (`gpt-5.6`, `gpt-5.4`, `gpt-5.4-mini`, ...).
- Examples 44, 49 and 59 use `LLMProfileStore()` with the default directory, so they write into the real `~/.openhands/profiles` (or `$OH_PERSISTENCE_DIR/profiles`); 44 and 49 delete their profiles at the end, but an interrupted run leaves them behind with secrets (`include_secrets=True`). Set `OH_PERSISTENCE_DIR` to a temp dir to isolate a run. Examples 37 and 39 use a temp dir.
- Example 44 saves the `gpt` profile with model `openhands/gpt-5.2` and no `base_url`, so the `LLM_API_KEY` must be valid for the OpenHands provider; example 49 hardcodes `openai/gpt-5.5` and `openai/prod/claude-sonnet-4-5-20250929` behind the All-Hands proxy, and 59 the same pair.
- The registry in `05_use_llm_registry.py` is a standalone object; the conversation builds its own `conversation.llm_registry`, and costs only show in `conversation_stats` for LLMs in that one.
- `19_llm_routing.py` defaults `LLM_ROUTING_SECONDARY_MODEL` to `LLM_MODEL`, so by default both routes hit the same model and the routing is visible only in logs.
- Fallback only triggers after the primary's own retries are exhausted (`num_retries=5`, backoff up to 64 s), so forcing a fallback by pointing the primary at a dead endpoint is slow; lower `num_retries` and `retry_*_wait` on the primary.
- `OH_PERSISTENCE_DIR` is read into module-level constants at import time; setting it after `import openhands.sdk` has no effect on the default profile directory.
- An LLM added to `conversation.llm_registry` before the first `send_message` is not tracked in `conversation_stats` (the stats subscribe to the registry when the agent is initialized, with no replay); add extra LLMs after the first message.
- `LLMProfileStore.list()` returns file names with the `.json` suffix, while `load`/`delete`/`switch_profile` take the bare name.
- `prompt_cache_key` is only sent for models LiteLLM marks as supporting it (sent for `gpt-4.1`, not for `gpt-4o-mini`), so a switch between such models changes whether the key appears at all.
