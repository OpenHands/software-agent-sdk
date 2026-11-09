# Observability, tracing and logging

A Python program gets OpenTelemetry traces of its conversations by setting environment variables before it imports the SDK: `LMNR_PROJECT_API_KEY` sends them to Laminar, and `OTEL_ENDPOINT`, `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` or `OTEL_EXPORTER_OTLP_ENDPOINT` send them to any OTLP collector through Laminar's exporter. With none of these set (and no user-side `Laminar.initialize()`), every `@observe` decorator is a pass-through and `lmnr` is not even imported. When enabled, each `Conversation` owns a long-lived root span named `conversation` whose trace session id is the conversation id, with `user_id`, `observability_metadata` (also as span attributes), `observability_tags`, `conversation.tags.*` attributes and an optional named child span; `conversation.send_message` / `run` / `arun`, `agent.step`, tool calls (one TOOL span per call with `tool_call_id` metadata), MCP calls, condenser and hook calls, and LLM calls (an `LLM` span carrying cost and cache buckets) nest under it, across threads and parallel tool workers. Sub-agent conversations start their own trace linked back to the parent. The `openhands.sdk.automation` helpers turn the generic `OPENHANDS_OBSERVABILITY_*` env vars an automation runner sets into `Conversation(...)` kwargs and HTTP headers. Separately, `get_logger(__name__)` returns standard loggers under a root configured on import from `LOG_*` env vars (Rich console, or JSON in CI or with `LOG_JSON`, optional rotating file), with LiteLLM, OpenAI, httpx and libtmux loggers quieted.

Source: `openhands-sdk/openhands/sdk/observability/`, `openhands-sdk/openhands/sdk/automation/`, `openhands-sdk/openhands/sdk/logger/`, `openhands-sdk/openhands/sdk/conversation/base.py`, `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`, `openhands-sdk/openhands/sdk/agent/agent.py`, `openhands-sdk/openhands/sdk/agent/parallel_executor.py`, `openhands-sdk/openhands/sdk/llm/`

Needs: `llm`, `network`

Examples: `examples/01_standalone_sdk/27_observability_laminar.py`

## Sub-features

- `S20.enable-gate`: `should_enable_observability()` is true when any of `LMNR_PROJECT_API_KEY`, `OTEL_ENDPOINT`, `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`, `OTEL_EXPORTER_OTLP_ENDPOINT` is set, or when the user already imported and initialized `lmnr`; the positive result is cached for the process. Unit: `tests/sdk/observability/test_laminar.py::test_should_enable_observability_returns_true_when_env_set`, `::test_should_enable_observability_returns_false_when_no_env`. Example: none. Real-LLM: none.
- `S20.lazy-import`: with observability off, decorated calls run unwrapped without importing `lmnr`; async functions keep their coroutine signature; a cold `import openhands.sdk` stays under 10 s (the guard against eager Laminar imports). Unit: `tests/sdk/observability/test_laminar.py::test_observe_preserves_async_signature`, `tests/sdk/test_import_performance.py`. Example: none. Real-LLM: none.
- `S20.laminar-init`: `maybe_init_laminar()` (run when `openhands.sdk.agent` is imported) calls `Laminar.initialize` once, passing `LMNR_BASE_URL` (unless empty), `LMNR_HTTP_PORT`/`LMNR_GRPC_PORT`, `LMNR_FORCE_HTTP` and `LMNR_INSTRUMENTS` (invalid names ignored with a warning), and skips when already initialized. Unit: `tests/sdk/observability/test_laminar.py::test_lmnr_base_url_passed_to_laminar`, `::test_lmnr_base_url_not_passed_when_empty`, `::test_maybe_init_laminar_skips_when_already_initialized`, `::test_lmnr_force_http_passed_to_laminar`, `::test_lmnr_instruments_passed_to_laminar`. Example: `examples/01_standalone_sdk/27_observability_laminar.py`. Real-LLM: none.
- `S20.otel-backend`: without `LMNR_PROJECT_API_KEY` but with an `OTEL_*` endpoint, Laminar initializes against the generic OTLP backend with browser-session, Patchright and Playwright instruments disabled. Unit: **none (untested)**. Example: none. Real-LLM: none.
- `S20.root-span`: each conversation starts a `conversation` root span (session id = conversation id, `user_id`, metadata, tags, attributes) that `@observe` methods re-attach in any thread or task, ends exactly once on `close()`, and does nothing when disabled; two concurrent conversations keep separate traces. Unit: `tests/sdk/observability/test_laminar.py` (`test_root_span_*`, `test_observe_calls_use_span_with_owner_root_span_*`, `test_two_concurrent_conversations_do_not_collide`), `tests/sdk/conversation/test_base_span_management.py`, `tests/sdk/conversation/local/test_span_double_ending.py`. Example: `examples/01_standalone_sdk/27_observability_laminar.py`. Real-LLM: none.
- `S20.conversation-args`: `Conversation(user_id=..., observability_metadata=..., observability_tags=..., observability_span_name=..., observability_parent_span_context=..., tags=...)` reach the root span: metadata and `conversation.tags.<key>` become attributes, a non-default span name becomes a child span, a parent context makes the trace a child of the caller's. Unit: `tests/sdk/conversation/test_base_span_management.py::test_base_conversation_passes_observability_metadata_and_tag_attributes`, `::test_base_conversation_uses_custom_observability_span_name_as_child_span`, `tests/sdk/conversation/test_conversation_factory.py::test_conversation_factory_forwards_local_observability_span_name`. Example: none. Real-LLM: none.
- `S20.env-conversation-defaults`: a local conversation also merges `OPENHANDS_OBSERVABILITY_METADATA` (JSON), uses `OPENHANDS_OBSERVABILITY_SPAN_NAME` when no span name is passed, and `OPENHANDS_OBSERVABILITY_PARENT_SPAN_CONTEXT` or `LMNR_SPAN_CONTEXT` as parent. Unit: **none (untested)** at the conversation level (the env readers are tested through `tests/sdk/test_automation_observability.py`). Example: none. Real-LLM: none.
- `S20.step-and-tool-spans`: `agent.step`/`agent.astep` spans contain one TOOL span per declared tool call, including rejected and cancelled calls, whose input is the action only and whose metadata carries `tool_call_id`. Unit: `tests/sdk/agent/test_tool_span_input.py`, `tests/sdk/agent/test_tool_span_tool_call_id_metadata.py`, `tests/sdk/observability/test_laminar.py::test_async_agent_and_conversation_paths_are_observed`. Example: none. Real-LLM: none.
- `S20.parallel-span-context`: parallel tool workers copy the context so their spans nest under the dispatcher, with and without `lmnr`, and overlapping tasks get fresh contexts. Unit: `tests/sdk/agent/test_parallel_tool_span_context.py`. Example: none. Real-LLM: none.
- `S20.llm-span`: each LLM call opens a current `LLM` span that records the authoritative proxy cost (matching `Metrics`), cache read and write buckets, closes on error and does not leak across retries. Unit: `tests/sdk/llm/test_llm_span_cost.py`. Example: none. Real-LLM: none.
- `S20.utility-operation-metadata`: side utilities (title generation and similar) mark their LLM spans with `openhands.operation` metadata (`OPERATION_METADATA_KEY`). Unit: `tests/sdk/conversation/test_utility_llm_span_metadata.py`. Example: none. Real-LLM: none.
- `S20.delegate-trace`: `detached_delegate_context()` makes a sub-agent conversation start a new trace instead of joining the parent's `task` span, yields `delegate.parent_trace_id`, `delegate.parent_span_id` and `tool_call_id`, restores the ambient span afterwards, and is a no-op when disabled. Unit: `tests/sdk/observability/test_laminar.py` (`test_detached_delegate_context_*`), `tests/tools/task/test_task_manager.py::TestTaskManager::test_delegate_metadata_includes_parent_span_link`. Example: none. Real-LLM: none.
- `S20.external-entry`: `init_laminar_for_external()` initializes Laminar and returns the current span context for a webhook-style caller to pass on. Unit: **none (untested)**. Example: none. Real-LLM: none.
- `S20.automation-context`: `automation_observability_context()` collects `OPENHANDS_OBSERVABILITY_METADATA`, `_TAGS` (comma list, deduplicated), `_SPAN_NAME` and `_PARENT_SPAN_CONTEXT` and merges caller additions. Unit: `tests/sdk/test_automation_observability.py::test_automation_observability_context_collects_generic_env`. Example: none. Real-LLM: none.
- `S20.automation-kwargs`: `automation_conversation_kwargs()` returns `Conversation(...)` kwargs with product `tags` built from `automation.*` metadata and the observability fields; an explicit span name overrides the env one. Unit: `tests/sdk/test_automation_observability.py::test_automation_conversation_tags_from_metadata`, `::test_automation_conversation_kwargs_include_product_and_observability_tags`, `::test_automation_conversation_kwargs_uses_env_span_name`, `::test_automation_conversation_kwargs_explicit_span_name_overrides_env`. Example: none. Real-LLM: none.
- `S20.automation-headers`: `automation_observability_headers()` / `observability_headers_from_env()` build `X-OpenHands-Observability-Metadata`, `-Tags`, `-Span-Name`, `-Parent-Span-Context` plus OTel propagation headers for direct agent-server calls; remote workspaces and remote conversations send them. Unit: `tests/sdk/test_automation_observability.py::test_automation_observability_headers_for_direct_api_calls`, `tests/sdk/workspace/remote/test_remote_workspace_mixin.py::test_headers_property_includes_observability_context`, `tests/sdk/workspace/remote/test_remote_workspace.py::test_send_completion_callback_includes_observability_headers`, `tests/sdk/conversation/remote/test_remote_conversation.py` (`-k observability`). Example: none. Real-LLM: none.
- `S20.get-env`: observability reads env vars straight from `os.environ`, never through python-dotenv's `find_dotenv()`. Unit: `tests/sdk/observability/test_utils.py`. Example: none. Real-LLM: none.
- `S20.logger-quiet-thirdparty`: importing the SDK sets LiteLLM and OpenAI loggers to ERROR (DEBUG only after a confirmed `DEBUG_LLM`), and httpx, httpcore and libtmux to WARNING, so LiteLLM INFO lines do not show. Unit: `tests/sdk/logger/test_litellm_log_suppression.py`. Example: none. Real-LLM: none.
- `S20.logger-config`: `get_logger(name)` returns a propagating stdlib logger; `setup_logging(...)` (auto-run on import unless `LOG_AUTO_CONFIG=false`) honors `LOG_LEVEL`, `DEBUG`, `LOG_JSON`, `CI`/`GITHUB_ACTIONS` (JSON console), `LOG_RICH_TRACEBACKS`, and `LOG_TO_FILE` with `LOG_DIR`, `LOG_ROTATE_WHEN`, `LOG_BACKUP_COUNT` (`<LOG_DIR>/app.log`), and does not add a second stream handler. Unit: **none (untested)**. Example: none. Real-LLM: none.
- `S20.rolling-log-view`: `rolling_log_view(logger, max_lines=...)` shows a live last-N-lines panel on a TTY, plain lines in CI, and one buffered JSON record on exit in JSON mode. Unit: **none (untested)**. Example: none. Real-LLM: none.

## How to get to it (agent POV)

- Env vars, set before `import openhands.sdk`: `LMNR_PROJECT_API_KEY`, `LMNR_BASE_URL`, `LMNR_HTTP_PORT`, `LMNR_GRPC_PORT`, `LMNR_FORCE_HTTP`, `LMNR_INSTRUMENTS`, `LMNR_SPAN_CONTEXT`; `OTEL_ENDPOINT`, `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_TRACES_HEADERS`, `OTEL_EXPORTER_OTLP_TRACES_PROTOCOL`, `OTEL_EXPORTER`. Covers `S20.enable-gate`, `S20.laminar-init`, `S20.otel-backend`.
- `Conversation(..., user_id=..., tags={...}, observability_metadata={...}, observability_tags=[...], observability_span_name="...", observability_parent_span_context="...")` (`S20.root-span`, `S20.conversation-args`).
- `openhands.sdk.observability`: `observe(name=..., span_type="TOOL", ...)` to trace a user function, `maybe_init_laminar()`, `init_laminar_for_external()`, `OPERATION_METADATA_KEY`.
- `OPENHANDS_OBSERVABILITY_METADATA` (JSON object), `OPENHANDS_OBSERVABILITY_TAGS`, `OPENHANDS_OBSERVABILITY_SPAN_NAME`, `OPENHANDS_OBSERVABILITY_PARENT_SPAN_CONTEXT`, and `openhands.sdk.automation`: `automation_observability_context`, `automation_conversation_kwargs`, `automation_conversation_tags`, `automation_observability_headers` (`S20.automation-*`, `S20.env-conversation-defaults`).
- `openhands.sdk.logger` (also `openhands.sdk.get_logger`): `get_logger`, `setup_logging`, `rolling_log_view`; env `LOG_LEVEL`, `DEBUG`, `LOG_JSON`, `LOG_TO_FILE`, `LOG_DIR`, `LOG_ROTATE_WHEN`, `LOG_BACKUP_COUNT`, `LOG_RICH_TRACEBACKS`, `LOG_AUTO_CONFIG`, `DEBUG_LLM` (`S20.logger-*`, `S20.rolling-log-view`).

## Driving it

Preconditions:
- `uv sync --dev` has run (`lmnr` and the OpenTelemetry protobuf packages are core dependencies); `control-sdk` is on `PATH` (`export PATH="$PWD/.agents/skills/verify-sdk/scripts:$PATH"`). `sdk-map run` does both.
- `tmux` on `PATH`. No external service: traces go to a local OTLP/HTTP collector the program runs (`programs/S20/otlp.py`, decoding `ExportTraceServiceRequest`), and the program sets the endpoint variables before it imports the SDK, as a user's program must. The `model` mode borrows the scripted OpenAI-compatible endpoint from `programs/S18/servers.py`.
- Live bullets: `DEEPSEEK_API_KEY`; example 27 also needs `LMNR_PROJECT_API_KEY`.

- **Traces of a conversation, settings passed to `Conversation(...)` (`S20.enable-gate`, `S20.otel-backend`, `S20.root-span`, `S20.conversation-args`, `S20.step-and-tool-spans`, `S20.parallel-span-context`).** `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` points at the local collector (generic OTLP backend, no Laminar key); a scripted agent runs one terminal call, then two in parallel (`tool_concurrency_limit=2`), then finishes.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S20/traced_conversation --arg args \
    --check status eq finished --check enabled eq true --check otlp_posts ge 1 \
    --check summary.traces eq 1 --check root.found eq true --check root.is_trace_root eq true \
    --check root_user_id eq user-7 --check root_session_id_is_conversation eq true \
    --check root_attr_ticket eq OPS-1 --check root_meta_ticket eq OPS-1 --check root_tag_team eq infra \
    --check root_tags contains nightly --check root_tags contains verify \
    --check summary.names contains nightly-run --check summary.names contains conversation.send_message \
    --check summary.names contains conversation.run --check summary.names contains agent.step \
    --check steps eq 3 --check terminal_spans eq 3 --check tool_spans eq 4 \
    --check tool_call_ids contains call_1_0 --check tool_call_ids contains call_1_1 \
    --check terminal_input_is_action eq true --check tools_under_step eq true --check parallel_terminal_parents eq 2
  ```
  The collector's root span `conversation` carries the session id (the conversation id), `user_id`, the metadata both as span attributes (`ticket`, `attempt`) and as trace metadata, the observability tags and `conversation.tags.team`; the custom span name is a child span `nightly-run`. Every TOOL span (three `TerminalAction`, one `FinishAction`) sits under an `agent.step`, carries its `tool_call_id`, and the two parallel calls share their step's span as parent.
- **Settings from the environment, and an external caller's span (`S20.env-conversation-defaults`, `S20.external-entry`).** A webhook-style handler opens a span, `init_laminar_for_external()` returns its context, and the runner hands it over in `OPENHANDS_OBSERVABILITY_PARENT_SPAN_CONTEXT` together with `OPENHANDS_OBSERVABILITY_METADATA` and `OPENHANDS_OBSERVABILITY_SPAN_NAME`; the conversation gets no observability arguments.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S20/traced_conversation --arg env \
    --check status eq finished --check external_context eq true \
    --check root.found eq true --check root_has_parent eq true --check parent_trace_joined eq true \
    --check root_automation_id eq auto-9 --check summary.names contains env-span \
    --check summary.names contains webhook-handler
  ```
- **Self-hosted Laminar backend (`S20.laminar-init`, `S20.enable-gate`).** `LMNR_PROJECT_API_KEY` with `LMNR_BASE_URL`/`LMNR_HTTP_PORT` aimed at the local collector, `LMNR_FORCE_HTTP=true`, and `LMNR_INSTRUMENTS=litellm,not-an-instrument`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S20/traced_conversation --arg laminar \
    --check status eq finished --check otlp_posts ge 1 --check auth_header eq "Bearer lmnr-verify-key" \
    --check root.found eq true --check steps eq 3 \
    --check warnings contains "Ignoring invalid LMNR_INSTRUMENTS value 'not-an-instrument'"
  ```
  The spans arrive over HTTP at `<LMNR_BASE_URL>:<LMNR_HTTP_PORT>` with the project key as Bearer token; the bad instrument name is logged and ignored.
- **LLM spans and utility calls (`S20.llm-span`, `S20.utility-operation-metadata`).** The agent's LLM is a real `LLM` (through LiteLLM) against the local scripted endpoint, which answers like a LiteLLM proxy with `x-litellm-response-cost: 0.0125` and 40 cached prompt tokens; afterwards the program calls `generate_title()` and `ask_agent()`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S20/traced_conversation --arg model \
    --check status eq finished --check llm_spans eq 5 --check llm_under_step eq 3 --check llm_under_conversation eq 5 \
    --check llm_costs len-eq 1 --check llm_costs.0 eq 0.0125 --check llm_cache_read.0 eq 40 \
    --check metrics_cost eq 0.05 --check title eq "Scripted title" --check answer eq "Scripted answer" \
    --check title_operation contains title_generation --check ask_operation contains ask_agent
  ```
  One `llm.<model>` LLM span per call, each holding the proxy's cost (the same figure `Metrics` accumulates: four agent-LLM calls, 0.05) and the cache-read bucket; the three agent calls nest under `agent.step`, the title and `ask_agent` calls under their own spans marked `openhands.operation`.
- **Sub-agent traces (`S20.delegate-trace`).** Inside a `task` TOOL span carrying a `tool_call_id`, a sub-agent conversation is created within `detached_delegate_context()`, as the task tool does.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S20/traced_conversation --arg delegate \
    --check status eq finished --check conversation_traces eq 2 \
    --check link_keys contains delegate.parent_trace_id --check link_keys contains delegate.parent_span_id \
    --check link_keys contains tool_call_id \
    --check delegate.task_span eq true --check delegate.child_new_trace eq true --check delegate.child_is_trace_root eq true \
    --check delegate.child_parent_trace_ok eq true --check delegate.child_tool_call_id eq call_task_1
  ```
- **Observability off (`S20.lazy-import`, `S20.enable-gate`, `S20.external-entry`).** No endpoint variables: the same conversation runs, nothing is exported, `lmnr` is never imported, and `init_laminar_for_external()` returns `None`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S20/traced_conversation --arg off \
    --check status eq finished --check enabled eq false --check lmnr_imported eq false \
    --check otlp_posts eq 0 --check root.found eq false --check external_context missing --check import_seconds lt 10
  ```
- **Automation helpers and headers (`S20.automation-context`, `S20.automation-kwargs`, `S20.automation-headers`, `S20.get-env`, `S20.enable-gate`).** The runner's `OPENHANDS_OBSERVABILITY_*` variables become `Conversation(...)` kwargs (used for a scripted conversation) and agent-server headers; a `RemoteWorkspace` aimed at a local capture server sends them on its command call and its completion callback.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S20/automation_env \
    --check context.observability_tags len-eq 3 --check context.observability_metadata.extra eq 1 \
    --check context.observability_span_name eq automation-run --check context.observability_parent_span_context eq parent-ctx-token \
    --check kwargs.tags.automationtrigger eq automation --check kwargs.tags.automationid eq auto-1 \
    --check kwargs.tags.automationrunid eq run-5 --check kwargs.tags.team eq infra \
    --check kwargs.observability_tags len-eq 2 --check kwargs.observability_span_name eq automation-run \
    --check explicit_span_name eq explicit-span \
    --check headers.X-OpenHands-Observability-Tags eq nightly,verify \
    --check headers.X-OpenHands-Observability-Span-Name eq automation-run \
    --check headers.X-OpenHands-Observability-Parent-Span-Context eq parent-ctx-token \
    --check conversation_tags.automationname eq Nightly --check conversation_tags.team eq infra \
    --check workspace_headers.x-openhands-observability-span-name eq automation-run \
    --check workspace_headers.x-openhands-observability-parent-span-context eq parent-ctx-token \
    --check workspace_headers.x-openhands-observability-metadata contains auto-1 \
    --check callback_headers.x-openhands-observability-span-name eq automation-run \
    --check exec_thread.value.0 eq automation-run --check exec_thread.error missing \
    --check gate.OTEL_ENDPOINT eq True --check gate.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT eq True \
    --check gate.OTEL_EXPORTER_OTLP_ENDPOINT eq True
  ```
  Reading the variables from code `exec`'d in a thread works (no `find_dotenv()` stack walk). `dotenv_enables` reports `True`: the SDK itself never reads `.env`, but importing it imports LiteLLM, which loads `./.env` into `os.environ` (see Gotchas).
- **Logging configuration (`S20.logger-config`, `S20.logger-quiet-thirdparty`, `S20.rolling-log-view`).** Fresh processes with different `LOG_*` environments log through `get_logger`, a LiteLLM and an httpx logger, and a `rolling_log_view`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S20/logging_setup \
    --check default.handlers contains RichHandler --check default.console_has_info eq true --check default.console_json_records eq 0 \
    --check default.propagate eq true --check default.litellm_level eq ERROR --check default.openai_level eq ERROR \
    --check default.litellm_line_shown eq false --check default.httpx_line_shown eq false \
    --check default.rolling_buffer len-eq 2 --check default.rolling_buffer.1 eq roll-3 \
    --check json_file.handlers contains TimedRotatingFileHandler --check json_file.console_json_records ge 5 \
    --check json_file.rolling_snapshot_records eq 1 --check json_file.file_exists eq true \
    --check json_file.file_has_info eq true --check json_file.file_is_json eq true \
    --check plain_file.file_has_info eq true --check plain_file.file_is_json eq false \
    --check ci.handlers len-eq 1 --check ci.console_json_records ge 1 --check ci.rolling_snapshot_records eq 0 \
    --check debug.root_level eq DEBUG --check debug.console_has_debug eq true \
    --check warning_level.console_has_info eq false --check no_auto_config.handlers len-eq 0
  ```
  Default: a Rich console handler; `LOG_JSON=1` a JSON stream handler and, with `LOG_TO_FILE`, a JSON `<LOG_DIR>/app.log`; `CI=true` JSON on the console; `DEBUG=true` lowers the root to DEBUG; `LOG_AUTO_CONFIG=false` adds no handler. In JSON mode `rolling_log_view` emits one record with the header and the last N lines on exit.
- **Live: a real model traced to the local collector (`S20.root-span`, `S20.llm-span`, `S20.step-and-tool-spans`).**
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk program S20/traced_conversation --arg live \
    --check status eq finished --check root.found eq true --check llm_spans ge 1 --check terminal_spans ge 1 \
    --check tools_under_step eq true
  ```
- **Example 27 against Laminar (`S20.laminar-init`, `S20.root-span`), blocked: needs `LMNR_PROJECT_API_KEY` (a Laminar project) and network to api.lmnr.ai.** The trace shows up in the Laminar dashboard with the conversation id as session.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output "All done" -- python examples/01_standalone_sdk/27_observability_laminar.py
  ```

## Gotchas

- Enablement is decided from env vars at import and at each decorated call, and `maybe_init_laminar()` runs when `openhands.sdk.agent` is imported: set `LMNR_PROJECT_API_KEY` / `OTEL_*` before importing the SDK, or call `Laminar.initialize()` yourself first.
- Once enabled, the process stays enabled (cached); tests that toggle it reset the module global.
- `27_observability_laminar.py` is in `_EXCLUDED_EXAMPLES`; it never runs in CI and does not print `EXAMPLE_COST`. Without a key it still runs the conversation, just without traces.
- `LOG_*` and `DEBUG_LLM` are read once at import. `DEBUG_LLM=true` calls `input()` at import to confirm, which blocks or raises `EOFError` in a non-interactive run.
- `setup_logging` never removes handlers and skips adding a stream handler if one exists (Uvicorn or a test runner may already have one), so `LOG_JSON` can appear to do nothing.
- `CI=true` or `GITHUB_ACTIONS` switches console logs to JSON.
- `tests/sdk/test_import_performance.py` measures import time and can be slow or flaky on a loaded machine; the S20 batch took about 50 s here.
- Agent-server telemetry (`tests/agent_server/telemetry/`) and server-side span routes belong to the server map, not here.
- Importing the SDK imports LiteLLM, which calls `load_dotenv()` on `./.env`: an `OTEL_*` or `LMNR_*` line in a `.env` in the program's working directory turns observability on even though the SDK itself never reads `.env`.
- `setup_logging` sets `httpx` and `libtmux` loggers to `LOG_LEVEL`, but they do not reach the SDK's handlers, so their INFO lines still do not show (their warnings go to Python's last-resort handler).
- `automation_observability_headers()` and `automation_conversation_kwargs()` deduplicate `OPENHANDS_OBSERVABILITY_TAGS`; the headers a `RemoteWorkspace` sends from the same variable do not (`nightly,verify,nightly`).
- `TestLLM` bypasses LiteLLM, so a scripted conversation produces no LLM spans; to see `llm.*` spans use a real `LLM` against an OpenAI-compatible endpoint (the `model` mode's local fake).
- Span attribute keys contain dots (`lmnr.association.properties.user_id`); control-sdk check paths split on every dot, so programs copy the ones a recipe checks to flat result fields.
