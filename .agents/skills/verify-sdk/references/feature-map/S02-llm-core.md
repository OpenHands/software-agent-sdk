# LLM core: completion, streaming, reasoning, metrics, retries

A Python program creates `LLM(model=..., api_key=..., base_url=..., usage_id=...)` and either hands it to an `Agent` or calls it directly. `llm.completion(messages=[Message(...)], tools=[...])` goes through LiteLLM's Chat Completions path and `llm.responses(...)` through the OpenAI Responses path; `llm.generate(...)`, which the agent uses, picks one from `api_mode` (`auto` resolves from model features). Either returns an `LLMResponse` whose `.message` is an SDK `Message` (text, tool calls, `reasoning_content`, Anthropic `thinking_blocks`, Responses reasoning items). With `stream=True` chunks reach an `on_token` callback (`Conversation(token_callbacks=[...])` in an agent run). Every call adds cost, token usage (prompt, completion, cache read and write, reasoning) and latency to `llm.metrics`; a conversation aggregates all its LLMs by `usage_id` in `conversation.conversation_stats`. Transient failures (rate limit, connection, timeout, empty response) are retried with exponential backoff, and provider errors are mapped to typed `LLMError` subclasses. Models without native tool calling (or with `native_tool_calling=False`) get tools described in the prompt and their text reply parsed back into tool calls. Per-model capability tables decide reasoning effort, thinking mode, sampling parameters, prompt caching, vision and the string serializer.

Source: `openhands-sdk/openhands/sdk/llm/llm.py`, `openhands-sdk/openhands/sdk/llm/llm_response.py`, `openhands-sdk/openhands/sdk/llm/message.py`, `openhands-sdk/openhands/sdk/llm/streaming.py`, `openhands-sdk/openhands/sdk/llm/_response_stream.py`, `openhands-sdk/openhands/sdk/llm/options/`, `openhands-sdk/openhands/sdk/llm/mixins/`, `openhands-sdk/openhands/sdk/llm/exceptions/`, `openhands-sdk/openhands/sdk/llm/utils/`, `openhands-sdk/openhands/sdk/conversation/conversation_stats.py`, `openhands-sdk/openhands/sdk/event/llm_convertible/`

Needs: `llm`, `network`

Examples: `examples/01_standalone_sdk/13_get_llm_metrics.py`, `examples/01_standalone_sdk/21_generate_extraneous_conversation_costs.py`, `examples/01_standalone_sdk/22_anthropic_thinking.py`, `examples/01_standalone_sdk/23_responses_reasoning.py`, `examples/01_standalone_sdk/29_llm_streaming.py`, `examples/01_standalone_sdk/36_event_json_to_openai_messages.py`

## Sub-features

- `S02.completion`: `llm.completion(messages, tools)` calls LiteLLM and returns an `LLMResponse` with the converted `Message`, tool calls and a metrics snapshot; custom params (temperature, top_p, max_output_tokens, seed, extra headers, `litellm_extra_body`) are forwarded. Unit: `tests/sdk/llm/test_llm_completion.py`, `tests/sdk/llm/test_llm_litellm_extra_body.py`. Example: `examples/01_standalone_sdk/13_get_llm_metrics.py`. Real-LLM: `tests/integration/tests/t01_fix_simple_typo.py`.
- `S02.api-mode`: `generate()` / `agenerate()` dispatch to Chat Completions or Responses by `api_mode` (`auto`, `chat`, `responses`); `uses_responses_api()` reports the choice. Unit: `tests/sdk/llm/test_llm.py::test_generate_dispatches_to_configured_api`, `::test_agenerate_dispatches_to_configured_api`. Example: `examples/01_standalone_sdk/36_event_json_to_openai_messages.py`. Real-LLM: none.
- `S02.streaming`: with `stream=True` and an `on_token` callback, content, reasoning and tool-call chunks arrive as `ModelResponseStream` deltas and the final message (tool calls and cache-read tokens included) is reassembled; sync and async generators are accepted. Unit: `tests/sdk/llm/test_llm_completion.py::test_llm_completion_streaming_with_callback`, `::test_llm_completion_streaming_with_tools`, `::test_llm_streaming_preserves_cache_read_tokens`, `tests/sdk/event/test_streaming.py`, `tests/sdk/agent/test_stream_context.py`. Example: `examples/01_standalone_sdk/29_llm_streaming.py`. Real-LLM: none.
- `S02.stream-no-callback`: `stream=True` without a callback degrades to a normal, non-streaming result instead of failing. Unit: `tests/sdk/llm/test_llm_completion.py::test_llm_streaming_without_callback_degrades`. Example: none. Real-LLM: none.
- `S02.timeouts`: `timeout` (default 300 s, `None` disables) is passed to LiteLLM and enforced per attempt; `stream_idle_timeout` (default 300 s) aborts and retries a stalled async stream, and an exhausted stream timeout puts the conversation in ERROR. Unit: `tests/sdk/llm/test_llm_timeout.py`. Example: none. Real-LLM: none.
- `S02.responses-reasoning`: on the Responses path `reasoning_effort`, `reasoning_summary` and `enable_encrypted_reasoning` (default on) are sent only to reasoning models; output items, reasoning summaries and encrypted reasoning are parsed into the `Message` and replayed on the next turn; streamed Responses events are reconstructed with or without a callback. Unit: `tests/sdk/llm/test_responses_parsing_and_kwargs.py`, `tests/sdk/llm/test_responses_serialization.py`, `tests/sdk/llm/test_llm.py::test_enable_encrypted_reasoning_respects_flag_and_defaults_true`. Example: `examples/01_standalone_sdk/23_responses_reasoning.py`. Real-LLM: none.
- `S02.anthropic-thinking`: Anthropic thinking and redacted-thinking blocks (with or without signature) are kept on assistant messages and shown on `MessageEvent`/`ActionEvent`; the blocks of a tool-call reply are sent back unchanged, signature included, on the next request; `extended_thinking_budget` is clamped below `max_tokens`. Unit: `tests/sdk/llm/test_thinking_blocks.py`, `tests/sdk/llm/test_chat_options.py::test_extended_thinking_budget_clamped_below_max_tokens`, `tests/sdk/llm/test_joint_budget_clamp.py`. Example: `examples/01_standalone_sdk/22_anthropic_thinking.py`. Real-LLM: `tests/integration/tests/c01_thinking_block_condenser.py`.
- `S02.thinking-text-reply`: a reply without tool calls (text, or thinking only) keeps its thinking blocks on the `MessageEvent` with the provider's signature when no registered secret was masked in them, and they are sent back unchanged on the next request; a block whose text was masked has no signature (`S13.mask-thinking`). Unit: `tests/sdk/agent/test_response_dispatch.py::test_thinking_blocks_mask_registered_secret` (the masked case only). Example: none. Real-LLM: none.
- `S02.reasoning-content`: chat-path `reasoning_content` (DeepSeek-style) is captured on the `Message` and carried on the `ActionEvent`. Unit: `tests/sdk/llm/test_reasoning_content.py`. Example: `examples/01_standalone_sdk/29_llm_streaming.py` (prints streamed reasoning). Real-LLM: none.
- `S02.reasoning-options`: per model family the chat options send `reasoning_effort` or adaptive thinking and strip `temperature`/`top_p` where the model rejects them (GPT-5, Claude Opus 4.5 and Sonnet 4.6+, Kimi), keep them for non-reasoning models, rename Azure `max_completion_tokens`, and add `prompt_cache_key` and OpenRouter headers where supported. Unit: `tests/sdk/llm/test_chat_options.py`. Example: none. Real-LLM: none.
- `S02.model-features`: the feature table answers per model whether it supports reasoning effort, extended or adaptive thinking, sampling params, prompt cache (and key, retention), stop words, Responses API, vision and inline images, and whether to force string content; proxy prefixes are stripped before lookup. Each capability is decided by the first source that states it: the caller's `capability_overrides`, then what the endpoint's model info declares, then the SDK's own table, then discovery (LiteLLM's registry and name heuristics, which never outrank the SDK's table); `ModelFeatures.sources` names the source per capability (`caller`, `endpoint`, `sdk`, `litellm`, `discovery`). Unit: `tests/sdk/llm/test_model_features.py`, `tests/sdk/llm/test_model_info_proxy_lookup.py`, `tests/sdk/llm/test_model_canonical_name_resolution.py`. Example: none. Real-LLM: none.
- `S02.native-tool-calling`: `native_tool_calling` defaults to true and tools are sent as provider tool definitions; setting it false switches to the prompted path. Unit: `tests/sdk/llm/test_llm.py::test_llm_function_calling_enabled_by_default`, `::test_llm_function_calling_can_be_disabled`, `tests/sdk/llm/test_llm_completion.py::test_llm_completion_with_tools`. Example: none. Real-LLM: none.
- `S02.prompted-tool-calling`: on the prompted path tools are described in the system prompt with in-context examples, the model's text is parsed back into `MessageToolCall`s, and conversion errors raise `FunctionCallConversionError` / `FunctionCallValidationError`. Unit: `tests/sdk/llm/test_llm_fncall_converter.py`, `tests/sdk/llm/test_llm_completion.py::test_llm_completion_non_function_call_mode`, `::test_llm_completion_function_call_vs_non_function_call_mode`. Example: none. Real-LLM: none.
- `S02.metrics`: `llm.metrics` accumulates `accumulated_cost`, per-call `costs`, `token_usages` (prompt, completion, cache read, cache write, reasoning, context window), `response_latencies` and a cache hit rate; metrics merge, diff, deep-copy, reset and restore. Unit: `tests/sdk/llm/test_llm_metrics.py`, `tests/sdk/llm/test_llm_completion.py::test_llm_cost_tracking`, `::test_llm_token_usage_tracking`, `::test_llm_latency_tracking`, `tests/sdk/llm/test_llm.py::test_llm_reset_metrics`. Example: `examples/01_standalone_sdk/13_get_llm_metrics.py`. Real-LLM: none.
- `S02.conversation-stats`: `conversation.conversation_stats` keeps one `Metrics` per `usage_id` (agent, condenser, any LLM added to `conversation.llm_registry`) and `get_combined_metrics()` sums them; restored metrics keep tracking after a resume. Unit: `tests/sdk/conversation/test_conversation_stats.py`, `tests/sdk/llm/test_llm.py::test_conversation_stats_restore_then_track`. Example: `examples/01_standalone_sdk/21_generate_extraneous_conversation_costs.py`. Real-LLM: none.
- `S02.pricing-override`: `input_cost_per_token` / `output_cost_per_token` override LiteLLM pricing for cost calculation. Unit: `tests/sdk/llm/test_llm_pricing_passthrough.py`, `tests/sdk/llm/test_llm_span_cost.py`. Example: none. Real-LLM: none.
- `S02.retries`: rate-limit, connection and other transient errors are retried up to `num_retries` (default 5) with backoff from `retry_min_wait`/`retry_max_wait`/`retry_multiplier`; non-retryable errors raise at once; a retry listener sees each attempt. Unit: `tests/sdk/llm/test_api_connection_error_retry.py`, `tests/sdk/llm/test_llm.py::test_llm_retry_on_rate_limit`, `tests/sdk/llm/test_llm_retry_telemetry.py`, `tests/sdk/llm/test_auth_refresh_retry.py`. Example: none. Real-LLM: none.
- `S02.no-response-retry`: an empty provider response is retried with a temperature bump and finally raises `LLMNoResponseError`, on both chat and Responses paths, sync and async. Unit: `tests/sdk/llm/test_llm_no_response_retry.py`. Example: none. Real-LLM: none.
- `S02.error-mapping`: LiteLLM and HTTP errors map to `LLMContextWindowExceedError`, `LLMMalformedConversationHistoryError`, `LLMAuthenticationError`, `LLMRateLimitError`, `LLMTimeoutError`, `LLMServiceUnavailableError`, `LLMBadRequestError`, `LLMContentPolicyViolationError` and budget denials. Unit: `tests/sdk/llm/test_exception_mapping.py`, `tests/sdk/llm/test_exception_classifier.py`, `tests/sdk/llm/test_exception.py`, `tests/sdk/llm/test_budget_denial_http.py`. Example: none. Real-LLM: `tests/integration/tests/a01_unmatched_tool_use.py` (provider responses to malformed histories, run through `tests/integration/api_compliance/run_compliance.py`).
- `S02.prompt-caching`: with `caching_prompt=True` (default) cache markers or keys are added for supporting models, and a "prompt too small to cache" rejection is retried without caching. Unit: `tests/sdk/llm/test_prompt_caching_cross_conversation.py`, `tests/sdk/llm/test_llm_completion.py::test_completion_retries_without_caching_on_prompt_cache_too_small`. Example: none. Real-LLM: none.
- `S02.token-count`: `llm.get_token_count(messages)` counts tokens with LiteLLM or a chat-template tokenizer (`custom_tokenizer`), including tool definitions, with fallbacks when the tokenizer is unavailable. Unit: `tests/sdk/llm/test_llm.py::test_llm_token_counting`, `::test_llm_token_counting_includes_tools`. Example: none. Real-LLM: none.
- `S02.context-window-guard`: a model whose context window is below the SDK minimum raises `LLMContextWindowTooSmallError` unless `ALLOW_SHORT_CONTEXT_WINDOWS` is set. Without an explicit `max_output_tokens` the output budget a request asks for (`effective_max_output_tokens`) is the model's advertised output limit capped at 16384, and 16384 when none is advertised; an explicit `max_output_tokens` is used as given. Unit: `tests/sdk/llm/test_llm.py::test_llm_raises_error_on_small_context_window`, `::test_llm_respects_allow_short_context_windows_env_var`, `::test_advertised_output_limit_is_not_a_request_default`, `::test_explicit_max_output_tokens_not_overridden`, `::test_default_manual_thinking_budget_is_bounded`. Example: none. Real-LLM: none.
- `S02.message-formatting`: persisted events reload with `Event.model_validate_json`, `LLMConvertibleEvent.events_to_messages` merges them into `Message`s, and `llm.format_messages_for_llm` / `format_messages_for_responses` produce the provider payload (string serializer, vision, cache markers). Unit: `tests/sdk/event/test_events_to_messages.py`, `tests/sdk/llm/test_message.py`, `tests/sdk/llm/test_message_serialization.py`. Example: `examples/01_standalone_sdk/36_event_json_to_openai_messages.py`. Real-LLM: none.
- `S02.image-inline`: with `inline_image_urls` (auto for providers that reject URLs, such as Moonshot Kimi) http image URLs are fetched and sent as base64 `data:` URLs, bounded by `OH_INLINE_IMAGE_MAX_MB`, `OH_INLINE_IMAGE_FETCH_TIMEOUT_S`, `OH_INLINE_IMAGE_CACHE_BYTES`. Unit: `tests/sdk/llm/test_image_inline.py`. Example: none. Real-LLM: none.
- `S02.image-request-guard`: `completion`, `acompletion`, `responses` and `aresponses` raise `LLMBadRequestError` before any HTTP request when the latest user message carries an image and `vision_is_active()` is false (a model without vision, or `disable_vision=True`); `get_token_count` and `format_messages_for_llm` still work on those messages; an image only in an older message does not block the call and is left out of the request; `capability_overrides={"supports_vision": True}` sends the image. Unit: `tests/sdk/llm/test_capability_wire.py::test_disabled_vision_rejects_request_but_not_token_count`, `::test_image_reaches_http_boundary`, `tests/sdk/agent/test_non_multimodal_image_input.py::test_llm_image_guard_scoped_to_latest_user_message`. Example: none. Real-LLM: none.
- `S02.image-adapter-loss`: on the native `deepseek/` route, when LiteLLM's adapter drops an image from the request body the call is refused as `LLMBadRequestError` before anything is sent and is not retried; an adapter that delivers every image sends the request as usual. Unit: `tests/sdk/llm/test_capability_wire.py::test_native_deepseek_adapter_must_deliver_or_reject_images`. Example: none. Real-LLM: none.
- `S02.log-completions`: `log_completions_to_file=True` writes each request and response as JSON under `log_completions_folder`. `log_completions` is a deprecated alias for the same flag (deprecated in v1.63.0, removal scheduled for v1.68.0): either name sets both, passing the two with different values raises `ValueError`, and a dump carries both keys. Unit: `tests/sdk/llm/test_llm_log_completions_integration.py`, `tests/sdk/llm/test_completion_logging_aliases.py`. Example: none. Real-LLM: none.
- `S02.serialization`: an `LLM` dumps with `api_key` and other secrets masked, round-trips through JSON, `model_copy` keeps configuration while refreshing provider info, and `LLM.load_from_env(prefix="LLM_")` builds an `LLM` from `LLM_*` variables, casting types. Unit: `tests/sdk/llm/test_llm_serialization.py`, `tests/sdk/llm/test_llm.py::test_llm_model_copy_preserves_configuration`, `tests/sdk/test_settings.py::test_llm_load_from_env_rehydrates_subscription_runtime` (the only `load_from_env` test; subscription case). Example: none. Real-LLM: none.

## How to get to it (agent POV)

- `LLM(model=..., api_key=SecretStr(...), base_url=..., usage_id="agent")` from `openhands.sdk`; most behavior is constructor fields: `api_mode`, `stream`, `timeout`, `stream_idle_timeout`, `num_retries`, `retry_min_wait`, `retry_max_wait`, `retry_multiplier`, `reasoning_effort` (default `high`), `reasoning_summary`, `enable_encrypted_reasoning`, `extended_thinking_budget`, `native_tool_calling`, `caching_prompt`, `prompt_cache_retention`, `disable_vision`, `inline_image_urls`, `force_string_serializer`, `input_cost_per_token`, `output_cost_per_token`, `max_input_tokens`, `max_output_tokens`, `log_completions_to_file`, `log_completions_folder`, `custom_tokenizer`, `extra_headers`, `litellm_extra_body`, `capability_overrides`.
- `llm.completion(...)`, `llm.responses(...)`, `llm.generate(...)` and their async forms; `on_token=` per call. Covers `S02.completion`, `S02.api-mode`, `S02.streaming`, `S02.responses-reasoning`.
- `Conversation(agent=..., token_callbacks=[on_token])` streams an agent run. Covers `S02.streaming` inside the loop.
- `llm.metrics` (`accumulated_cost`, `accumulated_token_usage`, `token_usages`, `costs`, `response_latencies`), `llm.reset_metrics()`, `llm.restore_metrics(...)`; `conversation.conversation_stats.get_combined_metrics()`, `.usage_to_metrics`. Covers `S02.metrics`, `S02.conversation-stats`.
- `llm.vision_is_active()`, `llm.is_caching_prompt_active()`, `llm.uses_responses_api()`, `llm.effective_max_input_tokens`, `llm.get_token_count(messages)`, `llm.format_messages_for_llm(...)`, `llm.format_messages_for_responses(...)`.
- `LLMConvertibleEvent.events_to_messages(events)` and `Message`, `TextContent`, `ImageContent`, `ThinkingBlock`, `RedactedThinkingBlock`, `ReasoningItemModel` from `openhands.sdk.llm`.
- Exceptions from `openhands.sdk.llm.exceptions` (`LLMContextWindowExceedError`, `LLMNoResponseError`, ...). Covers `S02.error-mapping`.
- Env vars: `ALLOW_SHORT_CONTEXT_WINDOWS`, `OH_INLINE_IMAGE_MAX_MB`, `OH_INLINE_IMAGE_FETCH_TIMEOUT_S`, `OH_INLINE_IMAGE_CACHE_BYTES`, `LOG_DIR` (default completions folder), and the `LLM_*` set read by `LLM.load_from_env`.

## Driving it

Preconditions:
- `uv sync --dev` has run; `control-sdk` is on `PATH` (`export PATH="$PWD/.agents/skills/verify-sdk/scripts:$PATH"`). `sdk-map run` does both.
- `tmux` on `PATH` (the agent runs use `TerminalTool`).
- Live bullets: `DEEPSEEK_API_KEY` exported.

The scripted bullets point a real `LLM` (`openai/...`, `anthropic/...`) at `programs/S02/fake_provider.py`, a local endpoint that answers Chat Completions, Responses and Anthropic Messages requests from a script. `TestLLM` would bypass LiteLLM; this way the SDK's own request building, parsing, streaming, retries, error mapping, metrics and logs run, and the program reports what reached the wire.

- **An agent run through the provider path (`S02.completion`, `S02.native-tool-calling`, `S02.metrics`, `S02.pricing-override`, `S02.log-completions`, `S02.reasoning-content`, `S02.api-mode`).** A priced `openai/gpt-4o-mini` with custom sampling params, headers, extra body and completion logging runs a two-step task (a terminal call with `reasoning_content`, then `finish`).
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S02/provider_run --arg chat \
    --check execution_status eq finished --check actions.0 eq terminal --check actions.1 eq finish \
    --check observation_text contains provider-run --check paths.0 eq /v1/chat/completions \
    --check uses_responses_api eq false --check request_tools contains terminal --check request_tools contains finish \
    --check sent.temperature eq 0.3 --check sent.top_p eq 0.9 --check sent.max_completion_tokens eq 123 \
    --check sent.seed eq 7 --check sent.verify_extra eq yes --check header_forwarded eq true \
    --check accumulated_cost eq 0.28 --check costs eq 2 --check token_usages eq 2 \
    --check response_latencies eq 2 --check prompt_tokens eq 200 --check log_files eq 2 \
    --check reasoning_content_on_action eq "think about echo"
  ```
  Native tool calling sends `terminal`, `finish` and `think` as provider tool definitions; two calls at 100 prompt and 20 completion tokens, priced at 0.001 and 0.002 per token, cost exactly 0.28 (the `input_cost_per_token`/`output_cost_per_token` override); `log_completions_to_file` wrote one JSON file per call.
- **Responses API and reasoning replay (`S02.api-mode`, `S02.responses-reasoning`).** `openai/gpt-5-mini` with `api_mode="responses"`, `reasoning_effort="low"` and `reasoning_summary="auto"`; the first reply carries a reasoning summary and encrypted content.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S02/provider_run --arg responses \
    --check execution_status eq finished --check paths.0 eq /v1/responses --check uses_responses_api eq true \
    --check reasoning_sent.effort eq low --check reasoning_sent.summary eq auto \
    --check include contains reasoning.encrypted_content --check first_body_keys contains prompt_cache_key \
    --check action_reasoning_summary.0 eq "plan the echo" --check action_encrypted eq ENC-1 \
    --check replayed_reasoning eq 1 --check replayed_encrypted.0 eq ENC-1
  ```
  The second request replays the reasoning item, encrypted content included.
- **Streaming in an agent run and direct calls (`S02.streaming`, `S02.stream-no-callback`, `S02.reasoning-content`).** `LLM(stream=True)` with `Conversation(token_callbacks=[...])`, then direct sync and async calls.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S02/provider_run --arg stream \
    --check execution_status eq finished --check stream_requested eq true --check chunks gt 10 \
    --check delta_kinds contains content --check delta_kinds contains reasoning --check delta_kinds contains tool_call \
    --check reasoning_content_on_action eq "think about echo" --check action_command eq "echo provider-run"
  control-sdk program S02/llm_calls --arg streaming \
    --check no_callback.text eq "plain reply" --check no_callback.stream_sent missing \
    --check async_callback.text eq "async streamed reply" --check async_callback.chunks gt 3 \
    --check async_callback.cache_read_tokens eq 40 \
    --check sync_callback.text eq "sync streamed reply" --check sync_callback.tool_calls.0 eq lookup \
    --check sync_callback.tool_arguments.0 eq '{"q": 1}'
  ```
  Content, reasoning and tool-call deltas reach the callback and the final message is reassembled (tool arguments and cache-read tokens included); `stream=True` without a callback sends a plain request and returns the full reply.
- **A streamed Responses reasoning summary (`S02.streaming`, `S02.responses-reasoning`).** `openai/gpt-5-mini` with `api_mode="responses"`, `reasoning_summary="auto"` and `stream=True`; the reply streams a reasoning item (`response.reasoning_summary_text.delta` events) and then a message, and `llm.responses(..., on_token=...)` collects the deltas.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S02/llm_calls --arg responses-stream \
    --check final_text eq "the answer" --check final_summary.0 eq "plan the reply" --check content_text eq "the answer" \
    --check summary_as_reasoning eq "plan the reply"
  ```
  Expected: the summary reaches the callback as `delta.reasoning_content`, as chat-path reasoning does, and the answer as `delta.content`; the final message carries both. Known failure (reproduced 2026-11-12, [#6695](https://github.com/OpenHands/software-agent-sdk/issues/6695)): no summary delta reaches the callback at all (`summary_as_reasoning` is empty; every delta is the message's `content`, chunk id `msg_1`). With the installed LiteLLM (1.105.1) a `response.reasoning_summary_text.delta` event is parsed as a `GenericEvent`, never the `ReasoningSummaryTextDeltaEvent` that `LLM._process_stream_event` looks for, and that branch would emit it as `delta.content` rather than `delta.reasoning_content`. The final message still has the summary.
- **Cache-write tokens on the Responses path (`S02.metrics`).** The same program: the Responses reply reports `input_tokens_details.cache_write_tokens: 600`, and a Chat Completions reply reports `prompt_tokens_details.cache_creation_tokens: 600`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S02/llm_calls --arg responses-stream \
    --check chat_cache_write eq 600 --check responses_cache_read eq 200 --check responses_cache_write eq 600
  ```
  Expected: `TokenUsage.cache_write_tokens` is 600 on both paths. Known failure (reproduced 2026-11-12, [#6691](https://github.com/OpenHands/software-agent-sdk/issues/6691)): the Responses path records 0 (`normalize_usage` reads only `cached_tokens` from `input_tokens_details`); the chat path records 600.
- **Timeouts (`S02.timeouts`).** A reply slower than `timeout=1`, a stream that stalls longer than `stream_idle_timeout=1` on `acompletion`, and the same stall inside `await conversation.arun()`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S02/llm_calls --arg timeouts \
    --check timeout.error eq LLMTimeoutError --check timeout.seconds lt 3 \
    --check stream_idle.error eq LLMTimeoutError --check stream_idle.seconds lt 3 \
    --check conversation.status eq error --check conversation.error_events.0 eq LLMTimeoutError
  ```
- **Anthropic thinking (`S02.anthropic-thinking`, `S02.prompt-caching`).** `anthropic/claude-sonnet-4-5-20250929` with `extended_thinking_budget=200000` and `max_output_tokens=4096`; the first reply carries a signed thinking block.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S02/provider_run --arg anthropic \
    --check execution_status eq finished --check paths.0 eq /v1/messages \
    --check thinking_param.type eq enabled --check thinking_param.budget_tokens eq 4095 --check max_tokens eq 4096 \
    --check anthropic_beta contains interleaved-thinking --check action_thinking_blocks eq 1 \
    --check thinking_sent_back.0 eq sig-1 --check cache_control_markers eq 2
  ```
  The budget is clamped below `max_tokens`, the thinking block lands on the `ActionEvent` and goes back unchanged (signature included) on the next turn, and the system prompt and last user turn carry cache markers.
- **Thinking on a text reply (`S02.thinking-text-reply`).** The same model and no secrets: the first reply is text with a signed thinking block and no tool call; then a second user turn.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S02/provider_run --arg anthropic-text \
    --check execution_status eq finished --check agent_messages eq 2 \
    --check message_thinking.0 eq "considering the answer" \
    --check message_signatures.0 eq sig-1 --check thinking_sent_back.0 eq sig-1
  ```
  Expected: the `MessageEvent` holds the thinking block as the provider sent it, signature `sig-1`, and the second request carries it back in the assistant turn. Known failure (reproduced 2026-11-14, [#6837](https://github.com/OpenHands/software-agent-sdk/issues/6837), fix in [#6839](https://github.com/OpenHands/software-agent-sdk/pull/6839)): the thinking text is on the `MessageEvent`, but its signature is null although nothing was masked (`message_signatures` is `[null]`), and the unsigned block is left out of the second request (`thinking_sent_back` is empty). `_mask_secrets` clears the signature of every thinking block on a reply without tool calls, whether or not a secret matched.
- **Reasoning options and model features (`S02.reasoning-options`, `S02.model-features`).** The feature table for several models, and the chat parameters each model's request actually carried.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S02/llm_calls --arg features \
    --check features.openai/gpt-5-mini.reasoning_effort eq true --check features.openai/gpt-5-mini.responses_api eq true \
    --check features.openai/gpt-4o-mini.reasoning_effort eq false \
    --check features.anthropic/claude-sonnet-4-5-20250929.thinking_mode eq manual \
    --check features.anthropic/claude-sonnet-4-5-20250929.prompt_cache eq true \
    --check features.deepseek/deepseek-chat.vision eq false --check proxy_prefix_stripped eq true \
    --check 'features.deepseek/deepseek-v4\.1-flash.vision' eq true --check 'features.deepseek/deepseek-v4\.1-flash.force_string' eq false \
    --check 'features.litellm_proxy/deepseek-v4\.1-flash.vision' eq true \
    --check vision_precedence.sdk.vision eq true --check vision_precedence.sdk.source eq sdk \
    --check vision_precedence.registry.vision eq true --check vision_precedence.registry.source eq sdk \
    --check vision_precedence.endpoint.vision eq false --check vision_precedence.endpoint.source eq endpoint \
    --check vision_precedence.caller.vision eq true --check vision_precedence.caller.source eq caller \
    --check vision_precedence.unknown_model.source eq discovery \
    --check sent.gpt5.reasoning_effort eq high --check sent.gpt5.temperature missing --check sent.gpt5.top_p missing \
    --check sent.gpt4o.reasoning_effort missing --check sent.gpt4o.temperature eq 0.5 --check sent.gpt4o.top_p eq 0.9 \
    --check sent.azure.max_tokens eq true --check sent.azure.max_completion_tokens eq false \
    --check sent.openrouter.openrouter_headers eq true
  ```
  A reasoning model gets `reasoning_effort` (default `high`) and loses `temperature`/`top_p`; a non-reasoning model keeps them; Azure gets `max_tokens`. `deepseek-v4.1-flash`, which LiteLLM's registry does not mark as visual, is vision-capable from the SDK's own list, under a proxy prefix too, and keeps list content. For that model's vision, `sources` says `sdk`; LiteLLM registry metadata that says `supports_vision: false` does not change it, the same statement from the endpoint's model info does (`endpoint`), and a caller override beats the endpoint (`caller`); a model nobody describes resolves by `discovery`. (Prompt-cache keys show in the Responses bullet above.)
- **Prompted tool calling (`S02.prompted-tool-calling`, `S02.native-tool-calling`).** `native_tool_calling=False`: the agent's tools are described in the prompt and the model's `<function=...>` text becomes real tool calls.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S02/provider_run --arg prompted \
    --check execution_status eq finished --check actions.0 eq terminal --check actions.1 eq finish \
    --check observation_text contains provider-run --check tools_param_sent eq false \
    --check prompt_describes_terminal eq true --check prompt_has_call_format eq true
  control-sdk program S02/llm_calls --arg prompted \
    --check tools_param_sent eq false --check parsed.valid.tool_calls.0.0 eq finish \
    --check parsed.valid.tool_calls.0.1.message eq bye \
    --check parsed.unknown_tool.error eq FunctionCallValidationError \
    --check parsed.unknown_parameter.error eq FunctionCallValidationError
  ```
- **Retries (`S02.retries`).** A 429 then a 503 then success; a 400; a 429 that never clears; a budget denial.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S02/llm_calls --arg retries \
    --check transient.text eq hi --check transient.requests eq 3 --check transient.listener len-eq 2 \
    --check non_retryable.error eq LLMBadRequestError --check non_retryable.requests eq 1 \
    --check exhausted.error eq LLMRateLimitError --check exhausted.requests eq 3 \
    --check budget_denial.requests eq 1
  ```
  `num_retries=3` means three attempts in all; the retry listener sees each retry; a bad request and a budget denial are not retried.
- **No-response retry (`S02.no-response-retry`).** Empty replies on the chat path (a reply with no `choices`) and on the Responses path (a stream that ends without its completed event), with `temperature=0`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S02/llm_calls --arg no-response \
    --check responses.text eq hi --check responses.requests eq 2 --check responses.temperatures.1 eq 1.0 \
    --check responses_async_exhausted.error eq LLMNoResponseError --check responses_async_exhausted.requests eq 2 \
    --check chat.text eq hi --check chat.temperatures.1 eq 1.0 \
    --check chat_exhausted.error eq LLMNoResponseError
  ```
  Known failure (reproduced 2026-11-12, [#6657](https://github.com/OpenHands/software-agent-sdk/issues/6657)): the chat path behaves as documented, but on the Responses path a stream that ends without its completed event does not reach the SDK's `LLMNoResponseError` check. LiteLLM raises `APIConnectionError` first, the SDK retries it as a transient error without the temperature bump (`responses.temperatures` is `[0.0, 0.0]`), and an exhausted async call ends in `LLMServiceUnavailableError`. The unit tests mock the LiteLLM call, so they do not meet it.
- **Error mapping (`S02.error-mapping`).** Each provider error body, as an OpenAI-compatible endpoint sends it, and a dead endpoint.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S02/llm_calls --arg errors \
    --check mapped.context_window eq LLMContextWindowExceedError \
    --check mapped.malformed_history eq LLMMalformedConversationHistoryError \
    --check mapped.auth_401_incorrect_key eq LLMAuthenticationError --check mapped.auth_401_invalid_key eq LLMAuthenticationError \
    --check mapped.rate_limit eq LLMRateLimitError --check mapped.service_unavailable eq LLMServiceUnavailableError \
    --check mapped.bad_request eq LLMBadRequestError --check mapped.content_policy eq LLMContentPolicyViolationError \
    --check mapped.timeout eq LLMTimeoutError --check mapped.connection_refused eq LLMServiceUnavailableError
  ```
- **Prompt caching (`S02.prompt-caching`).** An Anthropic model whose first request is rejected as too small to cache.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S02/llm_calls --arg cache-too-small \
    --check caching_active eq true --check text eq uncached --check requests eq 2 \
    --check cache_markers.0 eq 2 --check cache_markers.1 eq 0 --check caching_off_markers eq 0
  ```
- **Token counting, the context-window guard and the default output budget (`S02.token-count`, `S02.context-window-guard`).**
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S02/llm_calls --arg token-count --check plain gt 5 --check with_tools gt 100 --check longer gt 200
  control-sdk program S02/llm_calls --arg context-guard \
    --check short_window_error eq LLMContextWindowTooSmallError --check configured_max_output missing \
    --check fallback_max_output eq 16384 \
    --check advertised_64000.advertised eq 64000 --check advertised_64000.configured missing \
    --check advertised_64000.effective eq 16384 --check advertised_64000.sent eq 16384 \
    --check advertised_8192.effective eq 8192 --check advertised_8192.sent eq 8192 \
    --check explicit_32000.effective eq 32000 --check explicit_32000.sent eq 32000
  control-sdk program S02/llm_calls --arg context-guard --env ALLOW_SHORT_CONTEXT_WINDOWS=true \
    --check short_window_error missing
  ```
  `max_input_tokens=1000` is refused unless `ALLOW_SHORT_CONTEXT_WINDOWS` is set. Three models registered with LiteLLM and no `max_output_tokens` on the `LLM`: one with only `max_tokens=200000` and one advertising `max_output_tokens=64000` both resolve to 16384, one advertising 8192 to 8192, and that is the `max_completion_tokens` the request carries; `max_output_tokens=32000` on the 64000 model is sent as 32000.
- **Messages from saved events (`S02.message-formatting`).** A scripted conversation's saved event files, reloaded and formatted for each payload shape.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  CID=$(control-sdk conversation new --script S01/echo_parallel_finish.json --field id)
  control-sdk conversation send "$CID" --text "Write greeting.txt" --run --check execution_status eq finished
  control-sdk program S02/format_messages --arg "$CID" \
    --check event_files eq 10 --check chat_roles.0 eq system --check chat_has_tool_messages eq true \
    --check chat_list_content eq true --check string_content_text_turns eq true --check string_content_image_turn eq list \
    --check vision_image_sent eq true --check no_vision_image_sent eq false \
    --check anthropic_cache_markers eq 2 --check openai_cache_markers eq 0 \
    --check responses_instructions eq true --check responses_item_types contains function_call_output
  ```
  `force_string_serializer=True` turns every text turn into a plain string but keeps the list form for the turn with an image (a string would drop it).
- **Inline images (`S02.image-inline`).** An image URL served by the local endpoint, with `inline_image_urls=True` and `False`; private hosts are refused unless `OH_INLINE_IMAGE_ALLOW_PRIVATE_HOSTS=1`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S02/llm_calls --arg inline-image --env OH_INLINE_IMAGE_ALLOW_PRIVATE_HOSTS=1 \
    --check inline.image_urls.0 matches '^data:image/png' --check inline.image_urls.0 contains base64 --check as_url.image_urls.0 matches '^http://'
  control-sdk program S02/llm_calls --arg inline-image \
    --check inline.error missing --check inline.image_urls.0 matches '^http://'
  ```
- **An image the model cannot see is refused before the request (`S02.image-request-guard`).** A user message with an image, sent through all four call forms to `openai/gpt-3.5-turbo` (no vision) and to `openai/gpt-4o-mini` with `disable_vision=True`; then the same image in an older user turn; then the controls.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S02/llm_calls --arg image-guard \
    --check unsupported.vision_active eq false --check unsupported.errors.completion eq LLMBadRequestError \
    --check unsupported.errors.acompletion eq LLMBadRequestError --check unsupported.errors.responses eq LLMBadRequestError \
    --check unsupported.errors.aresponses eq LLMBadRequestError --check unsupported.requests eq 0 \
    --check unsupported.message contains "Image-bearing request cannot be sent" \
    --check unsupported.token_count gt 0 --check unsupported.formatted_messages eq 2 \
    --check disabled.vision_active eq false --check disabled.errors.completion eq LLMBadRequestError \
    --check disabled.errors.aresponses eq LLMBadRequestError --check disabled.requests eq 0 --check disabled.token_count gt 0 \
    --check older_image.error missing --check older_image.requests eq 1 --check older_image.image_parts_sent eq 0 \
    --check override.vision_active eq true --check override.error missing --check override.requests eq 1 \
    --check override.image_parts_sent gt 0 --check vision_model.requests eq 1 --check vision_model.image_parts_sent gt 0
  ```
  The endpoint receives nothing for the eight refused calls, while token counting and formatting of the same messages still work. With `capability_overrides={"supports_vision": True}` the same model sends the image, as a vision model does.
- **An image the native DeepSeek adapter drops (`S02.image-adapter-loss`).** `deepseek/deepseek-v4.1-flash` (vision-capable, so the guard above lets it through) with `num_retries=3` and zero waits: an image in the user message, then an image in a tool result.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S02/llm_calls --arg image-adapter-loss \
    --check user_image.vision_active eq true --check user_image.delivered_or_refused_once eq true \
    --check user_image.error eq LLMBadRequestError --check user_image.retries eq 0 --check user_image.requests eq 0 \
    --check tool_image.delivered_or_refused_once eq true \
    --check tool_image.error eq LLMBadRequestError --check tool_image.retries eq 0 --check tool_image.requests eq 0
  ```
  Expected: with the installed LiteLLM (1.105.1) the adapter drops both images, so each call is refused once as `LLMBadRequestError` and the endpoint receives nothing. Known failure (reproduced 2026-11-14, [#6840](https://github.com/OpenHands/software-agent-sdk/issues/6840), fix in [#6841](https://github.com/OpenHands/software-agent-sdk/pull/6841)): nothing is sent (`requests` is 0), but the refusal is raised inside LiteLLM's HTTP client, LiteLLM wraps it as `InternalServerError`, and the SDK treats that as transient: each call is retried (`retries` is 2 with `num_retries=3`, so `delivered_or_refused_once` is false) and ends in `LLMServiceUnavailableError`. With the default retry settings that is minutes of backoff for a request that can never be sent.
- **The completion-log flag and its alias (`S02.log-completions`).**
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S02/llm_calls --arg log-alias \
    --check default.to_file eq false --check default.dump_alias eq false \
    --check new_name.to_file eq true --check new_name.dump_alias eq true --check new_name.warnings len-eq 0 \
    --check alias.error missing --check alias.to_file eq true --check alias.dump_to_file eq true --check alias.dump_alias eq true \
    --check both_equal.to_file eq true \
    --check conflict.is_value_error eq true --check conflict.message contains "Conflicting values for log_completions"
  ```
  `LLM(log_completions=True)` turns on `log_completions_to_file`; a dump has both keys with the same value; `log_completions=True` with `log_completions_to_file=False` is refused.
- **Serialization and env loading (`S02.serialization`).**
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S02/llm_calls --arg serialization \
    --env LLM_MODEL=openai/gpt-4o-mini --env LLM_API_KEY=sk-env --env LLM_NUM_RETRIES=2 \
    --env LLM_TEMPERATURE=0.25 --env LLM_NATIVE_TOOL_CALLING=false \
    --check api_key_dumped eq "**********" --check secret_in_json eq false --check exposed_key eq true \
    --check roundtrip_model eq openai/gpt-4o-mini --check roundtrip_temperature eq 0.2 \
    --check copy_temperature eq 0.7 --check copy_keeps_model eq true --check copy_keeps_key eq true \
    --check env.model eq openai/gpt-4o-mini --check env.num_retries eq 2 --check env.temperature eq 0.25 \
    --check env.native_tool_calling eq false --check env.key_set eq true
  ```
- **Conversation stats (`S02.conversation-stats`, `S02.metrics`).** A persisted priced conversation, a second LLM added to `conversation.llm_registry` after the run (as example 21 does), then a resume.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S02/conversation_stats \
    --check first_run.usage_ids contains secondary --check first_run.agent_cost eq 0.28 \
    --check first_run.secondary_cost eq 0.14 --check first_run.combined_cost eq 0.42 \
    --check first_run.combined_prompt_tokens eq 300 \
    --check resumed.restored_agent_cost eq 0.28 --check resumed.agent_cost_after eq 0.42 \
    --check resumed.agent_calls_after eq 3 --check resumed.status eq finished
  ```
- **The same calls, live (`S02.completion`, `S02.streaming`, `S02.metrics`, `S02.api-mode`).**
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk program S02/live_llm \
    --check completion_text ne "" --check streamed_text ne "" --check stream_chunks gt 1 \
    --check generate_text ne "" --check calls_recorded eq 2 --check prompt_tokens gt 0 --check latencies eq 2
  CID=$(control-sdk conversation new --field id)
  control-sdk conversation send "$CID" --run \
    --text "Use the terminal to run: echo live-ok > live.txt. Then finish." \
    --check execution_status eq finished
  control-sdk exec --expect-output live-ok -- cat live.txt
  ```
- **Examples 13, 21, 29 and 36 (`S02.metrics`, `S02.conversation-stats`, `S02.streaming`, `S02.message-formatting`).** Live.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output EXAMPLE_COST -- python examples/01_standalone_sdk/13_get_llm_metrics.py
  control-sdk exec --model-env --expect-output demo-secondary -- python examples/01_standalone_sdk/21_generate_extraneous_conversation_costs.py
  control-sdk exec --model-env --expect-output EXAMPLE_COST -- python examples/01_standalone_sdk/29_llm_streaming.py
  control-sdk exec --model-env -- python examples/01_standalone_sdk/36_event_json_to_openai_messages.py
  ```
- **API compliance of a real provider (`S02.error-mapping`).** Live. The eight malformed-history patterns of `tests/integration/tests/a*.py`, sent to the run's model with the compliance tests' own classes (their runner, `run_compliance.py`, knows only the All-Hands proxy models, and `run_infer.py` does not load `a*` tests).
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk program S02/api_compliance \
    --check patterns len-eq 8 --check rejections_typed eq true \
    --check results.unmatched_tool_use.error_type eq LLMMalformedConversationHistoryError \
    --check results.unmatched_tool_result.error_type eq LLMMalformedConversationHistoryError \
    --check results.duplicate_tool_call_id.error_type eq LLMMalformedConversationHistoryError \
    --check results.parallel_wrong_order.error_type eq LLMMalformedConversationHistoryError
  ```
  Known failure (reproduced 2026-11-12, [#6658](https://github.com/OpenHands/software-agent-sdk/issues/6658)): DeepSeek rejects all eight. Five come back as `LLMMalformedConversationHistoryError`; the three whose provider message is "Messages with role 'tool' must be a response to a preceding message with 'tool_calls'" (unmatched tool result, duplicate id, parallel results in the wrong order) come back as the generic `LLMBadRequestError`, so a caller that recovers from malformed histories by catching the specific error misses them.
- **Examples 22 and 23 and the thinking-block condenser test (`S02.anthropic-thinking`, `S02.responses-reasoning`), blocked.** Example 22 and `c01_thinking_block_condenser` need an Anthropic extended-thinking model and key; example 23 hardcodes `openhands/gpt-5-mini-2025-08-07` and needs an OpenHands provider key. The run's DeepSeek key reaches neither.

## Gotchas

- `22_anthropic_thinking.py` asserts `LLM_MODEL` is set and only shows thinking blocks on an Anthropic model; `23_responses_reasoning.py` ignores `LLM_MODEL` and hardcodes `openhands/gpt-5-mini-2025-08-07`, so it needs a key valid for the OpenHands provider (it also accepts `OPENAI_API_KEY`).
- `36_event_json_to_openai_messages.py` sets `LOG_JSON`, `LOG_TO_FILE`, `LOG_DIR`, `LOG_LEVEL` via `os.environ.setdefault` and writes `.conversations/` and a log dir in the repo root.
- `reasoning_effort` defaults to `high`, which costs extra tokens on reasoning models; set `"none"` or `None` to avoid it.
- `stream=True` without any `on_token` callback silently falls back to a non-streamed result; streaming does not show until a callback is wired (per call or `Conversation(token_callbacks=...)`).
- `ALLOW_SHORT_CONTEXT_WINDOWS` must be set for small local models or `LLM` construction fails with `LLMContextWindowTooSmallError`.
- `num_retries=5` with `retry_min_wait=8` and `retry_max_wait=64` means a dead endpoint can block for minutes before raising; lower them in tests.
- `LLM.load_from_env` has no general unit test; the only test covers the subscription rehydration case.
- Cost is 0 and LiteLLM warns `Cost calculation failed` for models it has no pricing for (seen in test output); set `input_cost_per_token`/`output_cost_per_token` for proxies.
- `openrouter_site_url`/`openrouter_app_name` have defaults, so `HTTP-Referer` and `X-Title` headers go to every provider, not only OpenRouter.
- `num_retries` is the total number of attempts, not extra retries; the temperature bump on an empty response applies only when the temperature is exactly 0.
- LiteLLM picks the exception class from the error body's `type`, not only the HTTP status: a 503 or 401 whose body says `invalid_request_error` surfaces as a bad-request error. Fake servers must set a type that matches the status.
- A chat reply with `"choices": []` is raised by LiteLLM as `InternalServerError` (no 'choices'), so the SDK treats it as a service error, not as an empty response, and does not bump the temperature.
- The `log_completions` alias warns (`DeprecatedWarning`) only once the installed SDK version reaches 1.63.0, the release its deprecation names; an older build accepts it silently. The conflict error is a pydantic `ValidationError`, which is a `ValueError`.
- The image guard looks only at the latest user message. Images in older turns or in tool results never raise; for a model without vision they are dropped from the request.
- The output budget default (at most 16384) applies to every model, so a model that advertises 64000 output tokens still gets 16384 per request until `max_output_tokens` is set.
- Inline images from `localhost`/private addresses are refused unless `OH_INLINE_IMAGE_ALLOW_PRIVATE_HOSTS=1`; `force_string_serializer` keeps the list form for turns that carry an image.
