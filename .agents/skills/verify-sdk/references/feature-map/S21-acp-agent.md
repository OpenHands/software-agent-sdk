# ACP agent (in-process)

A Python program can swap the built-in LLM loop for an external coding agent that speaks the Agent Client Protocol: `ACPAgent(acp_command=[...])` (for example `ACP_PROVIDERS["claude-code"].default_command`, an `npx` launch of `claude-agent-acp`) is passed to an ordinary `Conversation`. On the first `send_message()`/`run()` the SDK spawns that subprocess in the workspace, runs the `initialize` / `authenticate` / `new_session` (or `load_session` to resume) handshake, sets the session mode and model, and forwards the agent's `mcp_config`. Each `run()` sends one ACP `prompt` built from the user message (text and images, plus a one-time context suffix with skills, repo context and secret names) and turns the server's streamed `session_update` notifications into SDK events: tool-call events as they happen, then a `FinishAction` carrying the reply and reasoning, with token usage and cost recorded on a sentinel `LLM`'s metrics. OpenHands tools, condensers, critics and confirmation do not apply; the ACP server owns its tools, context window and permissions (the SDK auto-approves every permission request). `conversation.ask_agent(...)` forks the ACP session for a side question, `set_acp_model` / `Conversation.switch_acp_model` switch the model at runtime, secrets reach the subprocess env and are masked in everything relayed back, and `agent.close()` terminates the subprocess. Providers known to the registry: `claude-code`, `codex`, `gemini-cli`, `kimi-code`, `pi`, `opencode`, or a custom command.

Source: `openhands-sdk/openhands/sdk/agent/acp_agent.py`, `openhands-sdk/openhands/sdk/agent/acp_contracts.py`, `openhands-sdk/openhands/sdk/agent/acp_models.py`, `openhands-sdk/openhands/sdk/agent/acp_model_discovery.py`, `openhands-sdk/openhands/sdk/agent/acp_file_credentials.py`, `openhands-sdk/openhands/sdk/agent/acp_tracing.py`, `openhands-sdk/openhands/sdk/settings/acp_providers.py`, `openhands-sdk/openhands/sdk/settings/acp_install_catalog.py`, `openhands-sdk/openhands/sdk/settings/model.py`

Needs: `node`, `network`, `llm`

Examples: `examples/01_standalone_sdk/40_acp_agent_example.py`

## Sub-features

- `S21.construct`: `ACPAgent` requires `acp_command`, defaults to a sentinel LLM, no tools and no default tools, is frozen, stores `acp_args`, and propagates `acp_model` to the sentinel LLM's model and metrics; it accepts `mcp_config` and an `agent_context`, while custom tools or a condenser raise `NotImplementedError` in `init_state`. Unit: `tests/sdk/agent/test_acp_agent.py::TestACPAgentInstantiation`, `::TestACPAgentValidation`, `::TestACPAgentSerialization`. Example: `examples/01_standalone_sdk/40_acp_agent_example.py`. Real-LLM: none.
- `S21.provider-registry`: `ACP_PROVIDERS` describes each provider (default `npx` command, session-mode, model config mechanism, file secrets, data-dir env var); providers are detected by agent name or command; the install catalog pins package versions that match the registry and renders Docker install plans. Unit: `tests/sdk/settings/test_acp_providers.py`, `tests/sdk/settings/test_acp_install_catalog.py`; live: `tests/sdk/settings/test_acp_install_catalog_pins_live.py` (`-m acp_live`). Example: `examples/01_standalone_sdk/40_acp_agent_example.py`. Real-LLM: none.
- `S21.settings`: `ACPAgentSettings(acp_server=..., acp_model=..., acp_command=...)` resolves the default or pinned-binary command (custom servers need an explicit command), carries the provider key and `acp_isolate_data_dir`, and `create_agent()` returns an `ACPAgent`. Unit: `tests/sdk/test_settings.py` (`test_acp_*`). Example: none. Real-LLM: none.
- `S21.handshake-live`: every built-in provider's real `npx` server completes initialize, authenticate (with a bogus key), new_session, session mode and model selection, and reports models consistent with the registry. Unit: `tests/sdk/agent/test_acp_conformance.py` (`-m acp_live`, needs `npx` and the npm registry). Example: none. Real-LLM: none.
- `S21.init-state`: `init_state` starts the server, emits a placeholder `SystemPromptEvent` with the context suffix as dynamic context (including secret names), and surfaces auth-required, spawn and other init failures as a classified `ConversationErrorEvent` with truncated detail before re-raising; a failed init leaves the agent reusable. Unit: `tests/sdk/agent/test_acp_agent.py::TestACPAgentInitState`, `::TestClassifyACPInitError`, `::TestACPErrorDetail`, `::TestACPAgentCleanup::test_failed_init_keeps_agent_reusable`. Example: none. Real-LLM: none.
- `S21.auth-selection`: `_select_auth_method` prefers file-backed logins (`chat-gpt` with a ChatGPT `auth.json`, `vertex-ai` with `GOOGLE_APPLICATION_CREDENTIALS`, `oauth-personal`) over API keys present in the env, and logs why no method could be performed. Unit: `tests/sdk/agent/test_acp_agent.py::TestSelectAuthMethod`, `::TestPreconfiguredCredentials`, `::TestAuthSelectionFailureReason`, `::TestUnperformableAuthMethodLogging`. Example: none. Real-LLM: none.
- `S21.step`: `step`/`astep` send one prompt (all text blocks and images, skill catalog and repo context only on the first turn) and emit a `FinishAction` event with the reply and reasoning, set FINISHED, fall back to placeholder text when the server sends none, stream tokens to `on_token`, and set ERROR on failure. Unit: `tests/sdk/agent/test_acp_agent.py::TestACPAgentStep`, `::TestACPAgentAstep`, `::TestImageUrlToAcpBlock`. Example: `examples/01_standalone_sdk/40_acp_agent_example.py`. Real-LLM: none.
- `S21.tool-call-events`: tool calls reported by the server are emitted as events live during the turn and before the final message, progress updates collapse into one entry, duplicates are dropped, oversized tool content is truncated, and in-flight calls are closed as failed on cancel. Unit: `tests/sdk/agent/test_acp_agent.py::TestACPToolCallEmission`, `::TestACPToolCallLiveEmission`, `::TestACPToolCallAccumulation`, `::TestACPToolCallProgressCollapse`, `::TestACPCancelInflightToolCalls`, `tests/sdk/agent/test_acp_dedup_and_truncation.py`. Example: `examples/01_standalone_sdk/40_acp_agent_example.py`. Real-LLM: none.
- `S21.permissions-auto-approve`: the client bridge answers every `request_permission` with the first option ("allow once"), and its filesystem and terminal client methods raise (the server uses its own). Unit: `tests/sdk/agent/test_acp_agent.py::TestOpenHandsACPClient::test_fs_methods_raise`, `::test_terminal_methods_raise`; the auto-approval itself is **none (untested)**. Example: `examples/01_standalone_sdk/40_acp_agent_example.py`. Real-LLM: none.
- `S21.timeouts-retry`: a prompt is aborted only after `acp_prompt_timeout` seconds without any server activity (activity resets the clock); startup has a hard `acp_startup_timeout`; connection errors and retriable ACP server errors are retried a bounded number of times, timeouts and other errors are not. Unit: `tests/sdk/agent/test_acp_agent.py::TestACPPromptIdleTimeout`, `::TestACPActivityHeartbeat`, `::TestACPPromptRetry`, `::TestClassifyACPTurnError`. Example: none. Real-LLM: none.
- `S21.cancel-pause`: cancelling `arun()` mid-prompt sends `session/cancel`, drains the prompt, emits failed tool calls, pauses without a turn error, and restarts the session on the next turn if the drain times out. Unit: `tests/sdk/agent/test_acp_agent.py::TestACPAgentAstep` (`test_astep_*cancel*`, `test_cleanup_interruption_*`). Example: none. Real-LLM: none.
- `S21.session-resume`: the ACP session id is persisted in `state.agent_state` and used with `load_session` when the conversation is reloaded; `acp_resume_session_id` overrides it; a failed load falls back to a new session; session ids are redacted in logs and serialization. Unit: `tests/sdk/agent/test_acp_agent.py::TestACPSessionIdPersistence`, `::TestHasLiveACPSession`. Example: none. Real-LLM: none.
- `S21.model-selection`: `acp_model` is applied through `set_config_option` (codex, claude; codex splits reasoning effort) or `set_session_model`, reapplied on resume, without silent fallback; `current_model_id` and `available_models` come from the server's session response. Unit: `tests/sdk/agent/test_acp_agent.py::TestMaybeSetSessionModel`, `::TestReapplySessionModelOnResume`, `::TestConfigOptionModelMechanism`, `::TestApplyAcpModel`, `::TestApplyAcpModelNoFallback`, `::TestExtractSessionModels`, `::TestACPAgentCurrentModelIdProperty`, `::TestACPAgentAvailableModelsProperty`, `tests/sdk/agent/test_acp_contracts.py`. Example: none. Real-LLM: none.
- `S21.model-switch`: `agent.set_acp_model(model)` switches a live session (an invalid model or unsupported provider raises `ValueError`); `Conversation.switch_acp_model(...)` defers before the session starts, persists the authoritative model and refreshes `current_model_id`. Unit: `tests/sdk/agent/test_acp_agent.py::TestSetACPModel`, `::TestACPAgentSupportsRuntimeModelSwitch`, `tests/sdk/conversation/test_switch_model.py` (`-k acp`). Example: none. Real-LLM: none.
- `S21.model-discovery`: `discover_acp_models(settings, secrets=...)` starts the server in a throwaway session and reports its default and offered models (config options or the legacy models block), passing secrets and reporting auth and spawn failures. Unit: `tests/sdk/agent/test_acp_model_discovery.py` (runs `tests/sdk/agent/fake_acp_models_server.py` as a real subprocess). Example: none. Real-LLM: none.
- `S21.ask-agent`: `conversation.ask_agent(question)` forks the ACP session, prompts the fork, records its token usage and cleans the fork state, leaving the main session untouched; it raises before the session exists. Unit: `tests/sdk/agent/test_acp_agent.py::TestACPAgentAskAgent`, `::TestClientForkTextRouting`. Example: `examples/01_standalone_sdk/40_acp_agent_example.py`. Real-LLM: none.
- `S21.usage-cost`: token usage and cost from `usage_update` notifications (or estimated from tokens) are recorded on the sentinel LLM's metrics per turn, incrementally, with latency and context window; partial metrics are kept on a usage timeout. Unit: `tests/sdk/agent/test_acp_agent.py::TestACPAgentTelemetry`, `::TestExtractTokenUsage`, `::TestEstimateCostFromTokens`. Example: `examples/01_standalone_sdk/40_acp_agent_example.py` (prints `EXAMPLE_COST`). Real-LLM: none.
- `S21.mcp-forwarding`: the agent's `mcp_config` is converted to ACP stdio or HTTP/SSE servers (with headers) and passed to `new_session` and `load_session`; none gives an empty list. Unit: `tests/sdk/agent/test_acp_agent.py::TestMcpConfigToAcpServers`, `::TestACPMcpForwarding`. Example: none. Real-LLM: none.
- `S21.secrets-env`: conversation secrets and static secrets are injected into the subprocess env (empty or None values skipped); inherited npm env vars are stripped; env vars that conflict with the chosen auth are suppressed. Unit: `tests/sdk/agent/test_acp_agent.py::TestACPSecretsEnvInjection`, `::TestACPSecretRegistryEnvInjection`, `::TestACPEnvConflictSuppression`. Example: none. Real-LLM: none.
- `S21.file-secrets`: file-content credentials (`CODEX_AUTH_JSON`, `GOOGLE_APPLICATION_CREDENTIALS_JSON`, `KIMI_CODE_CONFIG_TOML`, `PI_AUTH_JSON`) are written to disk for the matching provider only, the env var pointed at them, rotated files written back and masked, and `acp_isolate_data_dir=True` gives the CLI a per-conversation data dir under `<persistence_dir>/acp/<provider>`. Unit: `tests/sdk/agent/test_acp_agent.py::TestACPFileSecretMaterialisation`, `::TestACPFileSecretProviderScoping`, `::TestACPDataDirIsolation`, `tests/sdk/agent/test_acp_file_credentials.py`. Example: none. Real-LLM: none.
- `S21.masking`: secret values are masked in relayed message and thought chunks, tool-call fields and terminal output, fork text and the persisted turn; a masking failure fails safe. Unit: `tests/sdk/agent/test_acp_agent.py::TestACPBridgeMasking`, `::TestMaskJsonValue`, `::TestACPStepMasksPersistedTurn`. Example: none. Real-LLM: none.
- `S21.tracing`: with observability on, each turn emits an `LLM` span whose output is the assistant message and child TOOL spans with output and the ACP call id, all marked ACP with the server name; the prompt is capped and masked (dropped if masking fails); tracing is inert when disabled and a broken backend never breaks the turn. Unit: `tests/sdk/agent/test_acp_tracing.py`. Example: none. Real-LLM: none.
- `S21.cleanup`: `close()` terminates the subprocess with a bounded kill wait, closes the executor, is idempotent and error-tolerant, and an atexit hook (weak, not retaining the agent) cleans up forgotten agents. Unit: `tests/sdk/agent/test_acp_agent.py::TestACPAgentCleanup`, `::TestFilterJsonrpcLines`. Example: `examples/01_standalone_sdk/40_acp_agent_example.py` (calls `agent.close()`). Real-LLM: none.
- `S21.live-turn`: a full conversation against a real ACP server (text turn, image turn, `ask_agent`) produces a reply and a non-zero cost. Unit: **none (untested)**. Example: `examples/01_standalone_sdk/40_acp_agent_example.py`. Real-LLM: none (only the example on the `test-examples` label).

## How to get to it (agent POV)

- `from openhands.sdk.agent import ACPAgent`; `ACPAgent(acp_command=[...], acp_args=[...], acp_server="claude-code", acp_model="sonnet", acp_session_mode=None, acp_prompt_timeout=1800.0, acp_startup_timeout=90.0, acp_resume_session_id=None, acp_isolate_data_dir=False, acp_file_secrets=[...], mcp_config={...}, agent_context=AgentContext(...))`. Covers most sub-features.
- `from openhands.sdk.settings.acp_providers import ACP_PROVIDERS`; `ACP_PROVIDERS[key].default_command` (`S21.provider-registry`).
- `ACPAgentSettings(acp_server=..., ...).create_agent()` and `discover_acp_models(settings, secrets=...)` (`S21.settings`, `S21.model-discovery`).
- `Conversation(agent=acp_agent, workspace=cwd, secrets={...}, persistence_dir=...)`, `conversation.send_message(Message(... ImageContent(image_urls=[...])))`, `conversation.run()` / `await conversation.arun()`, `conversation.ask_agent(q)`, `conversation.switch_acp_model(...)`; `agent.set_acp_model(...)`, `agent.current_model_id`, `agent.available_models`, `agent.llm.metrics`, `agent.close()`.
- Env for the default Claude Code provider: `ANTHROPIC_API_KEY` and optionally `ANTHROPIC_BASE_URL` (Codex: `CODEX_API_KEY` / `OPENAI_API_KEY` or `~/.codex/auth.json`; Gemini: `GEMINI_API_KEY` or `GOOGLE_APPLICATION_CREDENTIALS`).
- Tests marker: `pytest -m acp_live` selects the live conformance and pin tests.

## Driving it

Preconditions:
- `uv sync --dev` has run; `control-sdk` is on `PATH` (`export PATH="$PWD/.agents/skills/verify-sdk/scripts:$PATH"`). `sdk-map run` does both.
- The scripted bullets need nothing else. The ACP server is `programs/S21/fake_acp.py`, a real stdio JSON-RPC subprocess that the SDK spawns through `ACPAgent(acp_command=[python, fake_acp.py])`. It streams thoughts, tool calls with progress, a permission request, reply chunks and `usage_update`s; behaves according to a tag in the prompt (`[slow]`, `[hang]`, `[flaky]`, `[fail]`, `[cancel]`, `[secret]`, `[big]`, `[silent]`); and journals every request it receives, so a program can check what the SDK sent as well as what it made of the replies.
- The live handshake bullet needs `npx` (Node 22 here) and the npm registry (exit 3 when either is missing). Example 40 needs `ANTHROPIC_API_KEY`.

- **A conversation through an ACP server (`S21.construct`, `S21.step`, `S21.tool-call-events`, `S21.permissions-auto-approve`, `S21.usage-cost`, `S21.mcp-forwarding`, `S21.secrets-env`, `S21.masking`, `S21.model-selection`, `S21.model-switch`, `S21.init-state`, `S21.cleanup`).** The program builds `ACPAgent(acp_command=..., acp_model="m2", mcp_config={stdio server, http server with headers}, agent_context=a skill)`. It runs a `Conversation` with `secrets={"FAKE_TOKEN": ..., "EMPTY_SECRET": ""}` and a token callback, sending one text + inline PNG turn and then a `[secret]` turn whose reply, thought and tool output echo the secret. It then switches the model with `switch_acp_model("m1")`, tries an unknown model, closes the agent twice, and switches the model on a second conversation before its first run. `npm_config_*` and `CLAUDECODE` are set in the parent's environment.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S21/acp_conversation --arg turn \
    --check before_start.tools eq 0 --check before_start.llm_model eq m2 --check before_start.current_model_id missing \
    --check status eq finished --check after_first.status eq finished \
    --check finish_messages.0 eq "Done: listed 2 files." --check finish_reasoning.0 eq "Thinking about turn 1." \
    --check after_first.tokens_streamed eq "Done: listed 2 files." \
    --check tool_events len-eq 6 --check tool_events contains t1-list:pending --check tool_events contains t1-list:completed \
    --check tool_events contains t1-read:completed --check tool_list_events_turn1 eq 2 \
    --check tool_list_final_output contains file_b --check tool_before_finish eq true \
    --check permission_answers len-eq 2 --check permission_answers.0.outcome.optionId eq allow_once \
    --check prompt_count eq 2 --check prompt1_types.1 eq image --check prompt1_image_mime eq image/png \
    --check prompt1_has_skill eq true --check prompt1_names_secret eq true --check prompt1_leaks_secret_value eq false \
    --check prompt2_blocks eq 1 --check prompt2_has_skill eq false \
    --check system_prompt_events eq 1 --check system_prompt_has_skill eq true --check system_prompt_names_secret eq true \
    --check session_new.0.mcp_servers.0.name eq files --check session_new.0.mcp_servers.0.command eq uvx \
    --check session_new.0.mcp_servers.0.env.0.name eq ROOT --check session_new.0.mcp_servers.1.type eq http \
    --check session_new.0.mcp_servers.1.headers.0.value eq verify \
    --check env_seen.FAKE_TOKEN eq tok-s3cret-4417 --check env_seen.EMPTY_SECRET missing \
    --check env_seen.npm_config_verify_marker missing --check env_seen.CLAUDECODE missing \
    --check secret_in_events eq false --check secret_in_tokens eq false --check secret_masked_in_finish eq true \
    --check finish_reasoning.1 contains secret-hidden \
    --check after_first.cost eq 0.25 --check cost_after_turns eq 0.5 --check prompt_tokens_after_turns eq 200 \
    --check completion_tokens eq 150 --check cache_read_tokens eq 30 --check context_window eq 200000 --check latencies eq 3 \
    --check after_first.current_model_id eq m2 --check after_first.available_models len-eq 2 \
    --check config_calls.0 eq model=m2 --check set_mode_calls eq 0 --check after_first.supports_switch eq false \
    --check switched_model eq m1 --check switched_current eq m1 --check switched_llm_model eq m1 \
    --check bad_switch contains "rejected set_config_option" \
    --check deferred.acp_model eq m2 --check deferred.current_model_id eq m2 --check deferred.config_calls.0 eq model=m2 \
    --check persisted_session_id eq true \
    --check alive_before_close eq true --check alive_after_close eq false --check second_close eq ok
  ```
  What this shows:
  - Each turn sends one `session/prompt` with the text, the image as an ACP image block, and (first turn only) the context suffix carrying the skill and the secret's name but never its value.
  - The two tool calls appear as `ACPToolCallEvent`s before the turn's `finish` action. The first appears twice: started, then completed with its final output. The progress frames are collapsed and the duplicate terminal update is dropped.
  - The permission request is answered with the first option.
  - Cost comes from `usage_update` deltas and tokens from the prompt response (completion and cache figures include the `ask_agent` fork).
  - The secret reaches the subprocess env and is masked everywhere it comes back.
  - `close()` leaves no process behind and is idempotent.
  - `supports_runtime_model_switch` is false for an unrecognised server, yet `switch_acp_model` still works through `set_config_option`.
- **`ask_agent` forks the session (`S21.ask-agent`).** Same program. After the two turns, the conversation's `ask_agent` forks the main session and prompts the fork; the fork answers and reports `usage_update` (cost 0.05) plus token usage. `ACPAgent.ask_agent` on an agent with no session raises.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S21/acp_conversation --arg turn \
    --check agent_ask_before_start eq RuntimeError --check ask_answer eq "Fork answer: Which files did you list?" \
    --check forks eq 1 --check fork_parent_is_main eq true --check main_prompts_after_ask eq 2 \
    --check ask_prompt_tokens_delta eq 100 \
    --check ask_cost_delta eq 0.05 --check ask_warnings len-eq 0 --check ask_seconds lt 1.5
  ```
  Known failure (reproduced 2026-10-13, not yet filed): the fork's cost is lost and every `ask_agent` call stalls. The client bridge (`_OpenHandsACPBridge.session_update`) routes every update for the fork session to the fork branch, which keeps only message chunks and `return`s before the `UsageUpdate` branch. As a result:
  - `ask_agent` waits the full `ACP_USAGE_UPDATE_TIMEOUT` and logs `UsageUpdate not received within 2.0s for fork session ...`.
  - It records no cost: `ask_cost_delta` is 0.0, `ask_seconds` is 2.01, and `ask_warnings` holds that line.
  - Token usage from the prompt response is still recorded.
- **Resume a persisted session (`S21.session-resume`, `S21.model-selection`).** A conversation with `persistence_dir` runs a turn and closes. The program then reopens it by id with a new `ACPAgent` (a new subprocess). A third agent carries `acp_resume_session_id` with no saved state, and a fourth an id the server has forgotten.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S21/acp_conversation --arg resume \
    --check first_error missing --check second_error missing --check new_process eq true \
    --check load_calls eq 1 --check load_used_persisted_id eq true --check load_known eq true --check session_new_calls eq 1 \
    --check same_session_after_resume eq true --check both_turns_same_session eq true \
    --check model_reapplied_after_load.0 eq model=m2 --check second_prompt_blocks eq 1 --check second_status eq finished \
    --check resume_id_serialized eq "**********" --check resume_id_plaintext_roundtrip eq true \
    --check explicit_error missing --check explicit_loaded.0 eq true \
    --check forgotten_error missing --check forgotten_load_known.0 eq false --check forgotten_new_sessions eq 1 \
    --check forgotten_status eq finished --check forgotten_warning eq true --check session_id_in_warnings eq false
  ```
  The resumed process calls `session/load` with the id saved in `agent_state`, reapplies the model, and does not resend the context suffix. A forgotten id falls back to `session/new`, and the warning names only a fingerprint of the id.
- **Startup failures (`S21.init-state`).** Four cases:
  - The server answers `session/new` with `-32000 Authentication required`; the user then sets the key and runs the same conversation again.
  - The command does not exist.
  - The server stalls in `initialize` past `acp_startup_timeout=2`.
  - The agent is given custom tools.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S21/acp_conversation --arg errors \
    --check cases.auth_required.errors.0.code eq ACPAuthRequired --check cases.auth_required.status eq error \
    --check cases.auth_required.raised contains "Authentication required" \
    --check cases.auth_required.retry_error missing --check cases.auth_required.retry_status eq finished \
    --check cases.missing_binary.errors.0.code eq ACPSpawnError --check cases.missing_binary.raised contains FileNotFoundError \
    --check cases.startup_timeout.errors.0.code eq ACPStartupTimeout --check cases.startup_timeout.seconds lt 10 \
    --check cases.startup_timeout.leftover_processes eq 0 --check cases.auth_required.leftover_processes eq 0 \
    --check cases.custom_tools.raised contains NotImplementedError
  ```
- **Idle timeout and retries (`S21.timeouts-retry`).** `acp_prompt_timeout=2`:
  - A `[slow]` turn lasts 4 s but sends a thought every 0.5 s.
  - A `[flaky]` turn fails once with `-32603`.
  - A `[fail]` turn fails with `-32602`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S21/acp_conversation --arg timeouts \
    --check cases.slow.status eq finished --check cases.slow.seconds ge 3.5 --check cases.slow.prompts_sent eq 1 \
    --check cases.flaky.status eq finished --check cases.flaky.prompts_sent eq 2 --check cases.flaky.seconds ge 4 \
    --check cases.fail.status eq error --check cases.fail.prompts_sent eq 1 --check cases.fail.errors.0.code eq ACPPromptError \
    --check cases.fail.raised contains ConversationRunError
  ```
  Activity resets the idle clock. A `-32603` is retried after the first back-off (5 s). Any other error is not retried and ends the run with `ConversationErrorEvent(ACPPromptError)`.
- **A server that goes silent (`S21.timeouts-retry`).** `[hang]` sends nothing until it is cancelled.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S21/acp_conversation --arg timeouts \
    --check cases.hang.agent_messages.0 contains "ACP prompt timed out after 2s" \
    --check cases.hang.prompts_sent eq 1 --check cases.hang.cancel_sent eq 1 --check cases.hang.status eq error \
    --check cases.hang.seconds lt 5
  ```
  Known failure (reproduced 2026-10-13, not yet filed): a timed-out turn is sent again. `ACPAgent._step` handles the idle timeout by emitting the timeout message and setting `execution_status = ERROR` without raising. `LocalConversation.run()` stops only on `PAUSED`, `STUCK`, `FINISHED` or an exception, so the loop calls `step()` again, which re-sends the same user message. Here the server received 3 prompts (and 2–3 `session/cancel`s) over 6 s, until the stuck detector set `stuck`: `prompts_sent` 3, `status` `stuck`. With the default `acp_prompt_timeout` of 1800 s that is 90 minutes. With stuck detection off, the loop runs until `max_iteration_per_run`.
- **Cancel a running turn (`S21.cancel-pause`).** `arun()` runs as a task. The server starts a long tool call and waits, and the program cancels the task. Then it runs another turn.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S21/acp_conversation --arg cancel \
    --check status_after_cancel eq paused --check cancel_sent eq 1 --check long_tool_statuses contains failed \
    --check long_tool_failed eq true --check errors len-eq 0 --check event_kinds contains InterruptEvent \
    --check next_error missing --check next_status eq finished --check next_finishes eq 1 --check sessions_used eq 1
  ```
  The SDK sends `session/cancel` and drains the prompt (the server answers `cancelled`). It closes the in-flight tool card as failed and pauses without a turn error. The next turn reuses the session.
- **A Codex binary with file credentials (`S21.file-secrets`, `S21.auth-selection`, `S21.model-selection`, `S21.secrets-env`).** The server is launched by path as `<run>/bin/codex-acp`, the form a pinned install takes. It reports `codex-acp` and offers the auth methods `chat-gpt` and `api-key`. `acp_model="gpt-5.5/high"`, and `CODEX_HOME` points at an ambient directory. There are two runs:
  - `CODEX_API_KEY` only, no isolation.
  - A ChatGPT `CODEX_AUTH_JSON` plus `CODEX_API_KEY` and a Gemini `GOOGLE_APPLICATION_CREDENTIALS_JSON`, with `acp_isolate_data_dir=True`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S21/acp_conversation --arg codex \
    --check runs.api_key.status eq finished --check runs.api_key.auth_methods_used.0 eq api-key \
    --check runs.api_key.codex_home_is_ambient eq true --check runs.api_key.env_has_codex_api_key eq true \
    --check runs.api_key.config_calls.0 eq model=gpt-5.5 --check runs.api_key.config_calls.1 eq reasoning_effort=high \
    --check runs.api_key.session_modes.0 eq agent-full-access --check runs.api_key.current_model_id eq gpt-5.5/high \
    --check runs.api_key.supports_switch eq true \
    --check runs.chatgpt_file.status eq finished --check runs.chatgpt_file.auth_methods_used.0 eq chat-gpt \
    --check runs.chatgpt_file.codex_home_is_ambient eq false --check runs.chatgpt_file.codex_home_under_conversation eq true \
    --check runs.chatgpt_file.codex_home_files contains auth.json --check runs.chatgpt_file.auth_file_mode eq 0o600 \
    --check runs.chatgpt_file.env_has_codex_auth_blob eq false --check runs.chatgpt_file.env_has_google_creds_path eq false \
    --check runs.chatgpt_file.acp_dir.0 eq codex
  ```
  For Codex:
  - The combined model id is split into `model` and `reasoning_effort` config options, and the session mode is `agent-full-access`.
  - A ChatGPT `auth.json` wins over the API key.
  - With isolation, `CODEX_HOME` moves to `<persistence_dir>/acp/codex`, where `auth.json` is written with mode 0600; the blob itself never reaches the env.
  - Gemini's credential is not materialised for Codex.
- **Model discovery and settings (`S21.model-discovery`, `S21.settings`).** `discover_acp_models(ACPAgentSettings(acp_server="custom", acp_command=...))` runs against:
  - `configOptions` models;
  - the legacy `models` block;
  - a server that demands a key, without the key and then with it passed as a secret;
  - a missing binary.

  After that, `ACPAgentSettings.create_agent()` drives a turn.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S21/acp_conversation --arg discovery \
    --check config_options.agent_name eq fake-acp --check config_options.current_model_id eq m1 \
    --check config_options.models len-eq 2 --check legacy.models len-eq 2 --check legacy.current_model_id eq m1 \
    --check needs_key.error.code eq ACPAuthRequired --check with_key_secret.models contains premium \
    --check with_key_secret.error missing --check missing_binary.code eq ACPSpawnError \
    --check probe_dirs_left len-eq 0 --check leftover_processes eq 0 \
    --check custom_without_command contains "acp_command must be set" --check claude_default_command.0 eq npx \
    --check created.type eq ACPAgent --check created.acp_model eq m2 --check created.isolate eq true \
    --check created_status eq finished --check created_current_model eq m2
  ```
- **Traces of an ACP turn (`S21.tracing`).** `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` points at the local collector from `programs/S20/otlp.py`. The user's message contains the secret value.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S21/acp_traced \
    --check status eq finished --check turn_spans eq 1 --check turn_type eq LLM --check turn_agent_kind eq acp \
    --check turn_acp_server eq custom --check turn_acp_model eq m2 --check turn_under_conversation eq true \
    --check turn_output_has_reply eq true --check turn_input_masked eq true \
    --check tool_span_names contains "List files" --check tool_span_names contains "Read README" \
    --check tool_call_ids contains t1-list --check tools_under_turn eq true --check tool_output_recorded eq true \
    --check secret_in_acp_spans eq false
  ```
- **Provider registry (`S21.provider-registry`).** Reuses the S19 catalog program: launch commands pin the catalog versions, and detection works by agent name and by command. Then, over the network, every pinned package version must exist on npm.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S19/acp_catalog \
    --check providers len-eq 6 --check commands_pin_catalog.claude-code eq true --check commands_pin_catalog.codex eq true \
    --check by_agent_name.claude-agent-acp eq claude-code --check by_command.npx_codex eq codex --check by_command.custom missing
  control-sdk program S19/acp_catalog --arg npm --check pins ge 7 --check missing_pins len-eq 0
  ```
- **Live handshake with every built-in provider (`S21.handshake-live`, `S21.provider-registry`).** Network, no account. For each provider, `ACPAgentSettings(acp_server=KEY)` resolves the default `npx` command, and `discover_acp_models` runs the real server's handshake with a bogus API key. kimi-code also gets `KIMI_MODEL_NAME`/`KIMI_MODEL_API_KEY`, and opencode gets `OPENCODE_API_KEY`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S21/live_handshake --timeout 900 \
    --check all_ok eq true --check failed len-eq 0 \
    --check probes.claude-code.current_in_models eq true --check probes.codex.current_in_models eq true \
    --check probes.gemini-cli.current_in_models eq true --check probes.pi.current_in_models eq true
  ```
  Here all six providers passed in about 155 s on a cold npm cache: claude-agent-acp 0.63.0, codex-acp 1.10.0, gemini-cli 0.46.0, Kimi Code CLI 0.38.0, OpenCode 1.18.23 and pi-acp 0.0.33.
- **Example 40 with Claude Code (`S21.live-turn`, `S21.step`, `S21.ask-agent`, `S21.usage-cost`), blocked: needs `ANTHROPIC_API_KEY` (optionally `ANTHROPIC_BASE_URL` for a LiteLLM proxy), plus `npx` and network for the image URL.** Run it from the repo root. The agent lists the `openhands-sdk/openhands/sdk/agent/` files, describes the python.org image and answers the `ask_agent` question. The example prints `EXAMPLE_COST` twice, then `Done!`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk exec --expect-output "ask_agent response:" --expect-output "Done!" -- python examples/01_standalone_sdk/40_acp_agent_example.py
  ```

## Gotchas

- `pyproject.toml` `addopts` deselects `acp_live`; a plain `pytest tests/sdk/agent` silently skips the conformance probes. They are skipped (not failed) when `npx` or the npm registry is unreachable. CI runs them in a separate non-required `acp-live-tests` job.
- The first live run downloads the ACP packages through `npx` and is slow (about 2 minutes here for six providers); `acp_startup_timeout` (90 s) can trip on a cold npm cache for the example.
- Example 40 is not in `_EXCLUDED_EXAMPLES`, so it runs on the `test-examples` label; it needs network for `npx` and for the image URL, and its first prompt assumes the current directory is the repo root.
- The SDK auto-approves every ACP permission request: `set_confirmation_policy` and security analyzers do not gate ACP tool calls, and the server runs in `bypassPermissions` (claude) or `agent-full-access` (codex) mode by default.
- `agent.llm` is a sentinel; cost and tokens come from the server's usage notifications. Some servers report no cost, in which case it is estimated from tokens or stays 0.
- `ACPProviderInfo.available_models` and `default_model` are deprecated since 1.54.0 (removal in 1.59.0); ask the server through `discover_acp_models()` instead. The conformance test emits these `DeprecatedWarning`s.
- Inherited `npm_*` env vars are stripped from the subprocess env, so a parent `npm run` context does not leak into `npx`.
- Always call `agent.close()` (example 40 does it in `finally`); the atexit fallback is best-effort.
- `ACPAgent.mcp_config` takes the server map itself (`{"files": {"command": ...}, "search": {"url": ..., "headers": {...}}}`). The `{"mcpServers": {...}}` wrapper fails validation with `Extra inputs are not permitted`. Remote servers are forwarded only when the ACP server advertises `mcpCapabilities.http`/`sse` in `initialize`.
- `conversation.ask_agent()` before the first `run()` starts the ACP session (lazy init) and forks it. Only `ACPAgent.ask_agent()` on an agent with no session raises `RuntimeError`.
- Any `-32000` error is classified `ACPAuthRequired` (it is ACP's auth-required code), at init and per turn. A server that uses `-32000` for other failures is reported as an auth problem.
- `supports_runtime_model_switch` is false for an unrecognised server, but `switch_acp_model()` still issues `set_config_option(model)` and works when the server accepts it.
- A credential-free handshake with kimi-code needs `KIMI_MODEL_NAME` and `KIMI_MODEL_API_KEY` (its `KIMI_API_KEY` is read only from `config.toml`). Without them `session/new` returns `-32000 Authentication required`.
- ACP turn and tool spans mask secrets, but the generic `conversation.send_message` span records the user's message as typed, so a secret pasted into a message shows up there.
- When the ACP server sends a `usage_update` with `cost`, the SDK records the delta of the cumulative amount per session. A server that reports per-turn rather than cumulative cost is under-counted.
