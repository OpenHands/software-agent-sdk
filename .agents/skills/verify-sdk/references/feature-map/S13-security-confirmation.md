# Security analyzers, confirmation mode and secrets

A Python program decides, per conversation, whether the agent's tool calls run straight away or stop for approval. Every tool schema the agent sends carries a `security_risk` field (`LOW`, `MEDIUM`, `HIGH`), and the system prompt carries a risk policy (built in, or a custom file through `Agent(security_policy_filename=...)`). `conversation.set_security_analyzer(...)` picks who rates each pending action: the actor LLM's own label (`LLMSecurityAnalyzer`), local deterministic rules (`PatternSecurityAnalyzer`, `PolicyRailSecurityAnalyzer`, composed with `EnsembleSecurityAnalyzer`), a separate guardrail LLM (`ToolShieldLLMSecurityAnalyzer`) or the Gray Swan API (`GraySwanAnalyzer`); each verdict is recorded as a `SecurityAnalysisEvent`. `conversation.set_confirmation_policy(...)` (`NeverConfirm`, `AlwaysConfirm`, `ConfirmRisky(threshold, confirm_unknown)`) then decides: when it says confirm, `run()` returns with `execution_status == WAITING_FOR_CONFIRMATION`, the program reads the pending actions, and either calls `run()` again to execute them or `reject_pending_actions(reason)` to turn them into `UserRejectObservation`s. Secrets given to `Conversation(secrets=...)` or `conversation.update_secrets(...)` (plain strings, `StaticSecret`, `LookupSecret` URLs, or custom `SecretSource`s) are exported as env vars to terminal commands that reference them, listed by name in the system prompt, and replaced with `<secret-hidden>` in every tool observation, agent message, and analysis event before callbacks or persistence see them.

Source: `openhands-sdk/openhands/sdk/security/`, `openhands-sdk/openhands/sdk/secret/`, `openhands-sdk/openhands/sdk/conversation/secret_registry.py`, `openhands-sdk/openhands/sdk/agent/agent.py`, `openhands-sdk/openhands/sdk/agent/base.py`, `openhands-sdk/openhands/sdk/conversation/base.py`, `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`, `openhands-sdk/openhands/sdk/tool/tool.py`, `openhands-sdk/openhands/sdk/settings/model.py`, `openhands-sdk/openhands/sdk/utils/redact.py`

Needs: `llm`, `network`

Examples: `examples/01_standalone_sdk/04_confirmation_mode_example.py`, `examples/01_standalone_sdk/16_llm_security_analyzer.py`, `examples/01_standalone_sdk/32_configurable_security_policy.py`, `examples/01_standalone_sdk/47_defense_in_depth_security.py`, `examples/01_standalone_sdk/12_custom_secrets.py`

## Sub-features

- `S13.risk-levels`: `SecurityRisk` orders `LOW < MEDIUM < HIGH`, `is_riskier` is reflexive, comparing `UNKNOWN` raises, `max` works on concrete risks. Unit: `tests/sdk/security/test_security_risk.py`. Example: none. Real-LLM: none.
- `S13.policies`: `NeverConfirm` never and `AlwaysConfirm` always asks; `ConfirmRisky` asks at or above `threshold` (default `HIGH`, `UNKNOWN` not allowed as threshold) and for `UNKNOWN` when `confirm_unknown=True` (default); policies round-trip polymorphically. Unit: `tests/sdk/security/test_confirmation_policy.py`, `tests/sdk/security/defense_in_depth/test_pattern.py` (`ConfirmRisky().should_confirm`), `tests/sdk/agent/test_security_analysis_event.py`. Example: `examples/01_standalone_sdk/47_defense_in_depth_security.py`. Real-LLM: none.
- `S13.confirm-wait`: with a confirming policy, `run()` stops in `WAITING_FOR_CONFIRMATION` before executing; `ConversationState.get_unmatched_actions(events)` returns the pending actions; a message-only response does not wait; a lone `FinishAction` or `ThinkAction` never waits; pausing keeps the waiting status; `is_confirmation_mode_active` reflects analyzer plus non-Never policy. Unit: `tests/sdk/conversation/local/test_confirmation_mode.py`. Example: `examples/01_standalone_sdk/04_confirmation_mode_example.py`. Real-LLM: none.
- `S13.confirm-approve-reject`: calling `run()` again executes the pending actions; `reject_pending_actions(reason)` emits a `UserRejectObservation` per action and the agent continues. Unit: `tests/sdk/conversation/local/test_confirmation_mode.py::TestConfirmationMode::test_action_then_confirm_or_reject`. Example: `examples/01_standalone_sdk/04_confirmation_mode_example.py`. Real-LLM: none.
- `S13.risk-extraction`: the agent pops `security_risk` from tool arguments; read-only tools and conversations without an analyzer get `UNKNOWN`; an invalid label raises. Unit: `tests/sdk/agent/test_extract_security_risk.py`, `tests/sdk/agent/test_security_policy_integration.py::test_security_risk_param_ignored_when_no_analyzer`. Example: none. Real-LLM: none.
- `S13.llm-analyzer`: `LLMSecurityAnalyzer` returns the risk the actor LLM put on the `ActionEvent`; combined with `ConfirmRisky` only HIGH (and UNKNOWN) actions stop. Unit: `tests/sdk/security/test_llm_security_analyzer.py`. Example: `examples/01_standalone_sdk/16_llm_security_analyzer.py`. Real-LLM: none.
- `S13.analysis-event`: each confirmation check with an analyzer emits one `SecurityAnalysisEvent` (analyzer and policy class names, `risks` and `details` keyed by action id) before the wait, even under `NeverConfirm`; an analyzer exception rates the action `HIGH` with the error as detail; secrets in it are masked for callbacks and persistence. Unit: `tests/sdk/agent/test_security_analysis_event.py`, `tests/sdk/security/test_security_analyzer.py`. Example: none. Real-LLM: none.
- `S13.security-policy-prompt`: the default risk policy is in the system message; `security_policy_filename` swaps in a custom file inserted verbatim (not Jinja-rendered); `None` or an empty file disables it without leaking the default; risk-assessment instructions stay with or without an analyzer. Unit: `tests/sdk/agent/test_security_policy_integration.py`. Example: `examples/01_standalone_sdk/32_configurable_security_policy.py`. Real-LLM: none.
- `S13.pattern-analyzer`: `PatternSecurityAnalyzer` normalizes Unicode evasions (fullwidth, zero-width, bidi, controls), scans tool name and arguments for destructive, code-execution and fetch-to-exec signatures and all fields (including thought and summary) for injection; reasoning alone does not trip exec patterns; input is capped with arguments first. Unit: `tests/sdk/security/defense_in_depth/test_pattern.py`, `tests/sdk/security/defense_in_depth/test_field_cap.py`, `tests/sdk/security/defense_in_depth/test_adversarial.py`. Example: `examples/01_standalone_sdk/47_defense_in_depth_security.py`. Real-LLM: none.
- `S13.policy-rails`: `PolicyRailSecurityAnalyzer` returns HIGH for fetch piped to a shell, raw-disk `dd`/`mkfs`, and catastrophic `rm` of `/` or home, LOW for safe commands, using the tree-sitter shell AST (quoted, path-qualified and `sh -c` nested verbs resolved; depth bound and parse errors map to UNKNOWN). Unit: `tests/sdk/security/defense_in_depth/test_policy_rails.py`, `tests/sdk/security/defense_in_depth/test_shell_semantics.py`, `tests/sdk/security/defense_in_depth/test_shell_parser_bypasses.py`, `tests/sdk/security/test_shell_ast.py`, `tests/sdk/security/test_shell_parser.py`, `tests/sdk/security/test_shell_parser_node_shapes.py`. Example: `examples/01_standalone_sdk/47_defense_in_depth_security.py`. Real-LLM: none.
- `S13.ensemble`: `EnsembleSecurityAnalyzer(analyzers=[...])` takes the maximum concrete risk, fails closed to HIGH on a child exception, ignores UNKNOWN next to a concrete verdict unless `propagate_unknown=True`, rejects an empty list, and serializes with nested children. Unit: `tests/sdk/security/defense_in_depth/test_ensemble.py`, `tests/sdk/security/defense_in_depth/test_serialization.py`. Example: `examples/01_standalone_sdk/47_defense_in_depth_security.py`. Real-LLM: none.
- `S13.toolshield`: `ToolShieldLLMSecurityAnalyzer(llm=..., history_window, safety_experiences)` asks a separate guardrail LLM about the action plus recent history, wraps untrusted fields in escaped XML, accepts only one standalone risk label (smuggled or conflicting labels give UNKNOWN); helpers detect active MCP tools and load ToolShield safety experiences. Unit: `tests/sdk/security/test_toolshield_llm_analyzer.py` (7 tests skip without the `toolshield` extra). Example: none. Real-LLM: none.
- `S13.grayswan`: `GraySwanAnalyzer` reads `GRAYSWAN_API_KEY` / `GRAYSWAN_POLICY_ID`, sends recent events as OpenAI messages to the Gray Swan API, maps the violation score with `low_threshold`/`medium_threshold` (indirect prompt injection escalates to HIGH), and returns UNKNOWN on error, timeout or no key. Unit: `tests/sdk/security/grayswan/test_grayswan_analyzer.py`, `tests/sdk/security/grayswan/test_grayswan_utils.py`. Example: none. Real-LLM: none.
- `S13.settings-security`: `ConversationSettings(confirmation_mode=True, security_analyzer="llm")` builds `ConfirmRisky` plus `LLMSecurityAnalyzer` (no analyzer gives `AlwaysConfirm`; off gives `NeverConfirm`). Unit: `tests/sdk/test_settings.py::test_conversation_settings_create_request`. Example: none. Real-LLM: none.
- `S13.secret-sources`: `StaticSecret` and `LookupSecret` (URL plus headers, relative URLs resolved against the current server, `register_local_secret_resolver` short-circuits HTTP and falls back when it declines or raises) redact values and sensitive headers on dump unless exposed or encrypted with a cipher. Unit: `tests/sdk/conversation/test_secret_source.py`. Example: `examples/01_standalone_sdk/12_custom_secrets.py`. Real-LLM: none.
- `S13.secret-registry`: `SecretRegistry.update_secrets` adds or overwrites; `find_secrets_in_text` matches names case-insensitively; `get_secrets_as_env_vars(command)` resolves only referenced names (a failing source is skipped); resolved values are tracked for masking. Unit: `tests/sdk/conversation/test_secrets_manager.py`. Example: `examples/01_standalone_sdk/12_custom_secrets.py`. Real-LLM: none.
- `S13.secret-injection`: secrets from `Conversation(secrets=...)`, `update_secrets(...)` and `AgentContext.secrets` (request wins on collision) reach the terminal as env vars and appear by name in the dynamic system context. Unit: `tests/sdk/conversation/test_conversation_secrets_constructor.py`, `tests/cross/test_agent_secrets_integration.py`. Example: `examples/01_standalone_sdk/12_custom_secrets.py`. Real-LLM: none.
- `S13.mask-output`: `mask_secrets_in_output` replaces every tracked value with `<secret-hidden>`, survives a serialization round trip, tolerates and backs off failing sources; the streaming mask catches a value split across chunks. Unit: `tests/sdk/conversation/test_secrets_manager.py` (`test_mask_secrets_*`, `test_compile_stream_mask_*`), `tests/cross/test_agent_secrets_integration.py::test_mask_secrets`, `::test_masking_persists`. Example: `examples/01_standalone_sdk/12_custom_secrets.py`. Real-LLM: none.
- `S13.mask-tool-output`: every tool observation goes through `ToolDefinition.__call__`, which masks it with `mask_secrets_in_model`: file editor views, grep's echoed pattern, glob paths and nested apply-patch content are masked; no tool bypasses it; without a conversation nothing is masked. Unit: `tests/tools/test_tool_output_secret_masking.py`. Example: none. Real-LLM: none.
- `S13.mask-models`: `mask_secrets_in_model` walks nested models, lists, sets, dicts and namedtuples, leaves non-strings, `SecretStr` and enum members alone, and keeps base64 image data (data URLs, browser screenshots) intact while masking regular URLs. Unit: `tests/sdk/conversation/test_secret_registry_model_masking.py`, `tests/cross/test_secret_masking_tool_payloads.py`. Example: none. Real-LLM: none.
- `S13.mask-async`: in `arun()`, agent messages and tool thoughts are masked the same as in sync, including `LookupSecret`s resolved over loopback and secrets registered during a lookup, without blocking the loop. Unit: `tests/sdk/agent/test_async_secret_masking.py`. Example: none. Real-LLM: none.

## How to get to it (agent POV)

- `conversation.set_confirmation_policy(AlwaysConfirm() | NeverConfirm() | ConfirmRisky(threshold=SecurityRisk.MEDIUM, confirm_unknown=False))`, `conversation.set_security_analyzer(analyzer | None)`; then poll `conversation.state.execution_status`, `ConversationState.get_unmatched_actions(conversation.state.events)`, `conversation.reject_pending_actions(reason)`, `conversation.confirmation_policy_active` / `is_confirmation_mode_active`. Covers `S13.policies`, `S13.confirm-*`, `S13.analysis-event`.
- `openhands.sdk.security`: `LLMSecurityAnalyzer`, `PatternSecurityAnalyzer`, `PolicyRailSecurityAnalyzer`, `EnsembleSecurityAnalyzer(analyzers=[...], propagate_unknown=False)`, `ToolShieldLLMSecurityAnalyzer(llm=...)`, `GraySwanAnalyzer(api_key=..., policy_id=...)`, `SecurityRisk`, `SecurityAnalyzerBase` (subclass with `security_risk(action)` or `analyze_action(action) -> SecurityAnalysis`).
- `Agent(security_policy_filename="/abs/path.md" | None)`; `agent.static_system_message` shows the result (`S13.security-policy-prompt`).
- `ConversationSettings(confirmation_mode=..., security_analyzer="llm")` (`S13.settings-security`).
- Secrets: `Conversation(agent, secrets={"NAME": "value" | SecretSource})`, `conversation.update_secrets({...})`, `AgentContext(secrets=...)`; `openhands.sdk.secret`: `StaticSecret(value=...)`, `LookupSecret(url=..., headers=...)`, `SecretSource` (subclass `get_value`), `register_local_secret_resolver` / `unregister_local_secret_resolver`. Covers `S13.secret-*`, `S13.mask-*`.
- Env vars: `GRAYSWAN_API_KEY`, `GRAYSWAN_POLICY_ID`; example 04 reads `ADD_SECURITY_ANALYZER` (any non-empty value adds `LLMSecurityAnalyzer`).

## Driving it

Preconditions:
- `uv sync --dev` has run; `control-sdk` is on `PATH` (`export PATH="$PWD/.agents/skills/verify-sdk/scripts:$PATH"`). `sdk-map run` does both.
- `tmux` on `PATH` (the conversations use `TerminalTool`).
- No outside service is needed for the scripted bullets: `programs/S13/analyzers.py` serves a stand-in for the Gray Swan Cygnal endpoint and `programs/S13/secrets_run.py` a stand-in secrets service, both on `127.0.0.1`. The ToolShield guardrail is a second scripted LLM.
- Live bullets: `DEEPSEEK_API_KEY` exported.

- **Confirmation in a running conversation (`S13.confirm-wait`, `S13.confirm-approve-reject`, `S13.risk-extraction`, `S13.llm-analyzer`, `S13.analysis-event`, `S13.security-policy-prompt`).** A scripted agent under `AlwaysConfirm` without an analyzer: a message-only reply, two parallel terminal actions that wait (then `pause()`, then approved by `run()`), one action rejected with `reject_pending_actions`, then a lone finish. Under `LLMSecurityAnalyzer` + `ConfirmRisky()`: LOW runs, HIGH waits, an unlabeled action waits, an invalid label is an error, and a read-only tool's label is dropped. `NeverConfirm` still records the analysis; without an analyzer the label is discarded. An analyzer that raises with a conversation secret in its message. Then `agent.static_system_message` for the default, a custom, a `None`, an `""` and an empty-file policy.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S13/confirmation_flow \
    --check always.message_only_status eq finished \
    --check always.after_parallel eq waiting_for_confirmation --check always.pending len-eq 2 \
    --check always.files_before_approve len-eq 0 --check always.after_pause eq waiting_for_confirmation \
    --check always.files_after_approve len-eq 2 --check always.pending_after_approve.0 contains three.txt \
    --check always.after_reject eq idle --check always.rejections len-eq 1 --check always.rejections.0.reason eq "not now" \
    --check always.final eq finished --check always.three_written eq false --check always.analysis_events eq 0 \
    --check always.policy_active eq true --check always.mode_active eq false \
    --check risky.first_stop eq waiting_for_confirmation --check risky.pending_first.0 contains high.txt \
    --check risky.low_ran eq true --check risky.analysis_before_wait.analyzer eq LLMSecurityAnalyzer \
    --check risky.analysis_before_wait.policy eq ConfirmRisky --check risky.analysis_before_wait.risks.0 eq HIGH \
    --check risky.second_stop eq waiting_for_confirmation --check risky.pending_second.0 contains unlabeled.txt \
    --check risky.third_stop eq waiting_for_confirmation \
    --check risky.errors.0 contains "'SEVERE' is not a valid SecurityRisk" \
    --check risky.final eq finished --check risky.action_risks.0 eq LOW --check risky.action_risks.1 eq HIGH \
    --check risky.action_risks.2 eq UNKNOWN --check risky.action_risks.3 eq UNKNOWN \
    --check risky.high_written eq false --check risky.unlabeled_written eq true \
    --check risky.analyses len-eq 4 --check risky.mode_active eq true \
    --check never.status eq finished --check never.written eq true --check never.analyses.0.policy eq NeverConfirm \
    --check never.analyses.0.risks.0 eq HIGH --check never.policy_active eq false --check never.mode_active eq false \
    --check no_analyzer.status eq finished --check no_analyzer.action_risks.0 eq UNKNOWN --check no_analyzer.analyses eq 0 \
    --check raising.status eq waiting_for_confirmation --check raising.analyses.0.risks.0 eq HIGH \
    --check raising.analyses.0.details.0.error eq "backend refused token <secret-hidden>" \
    --check raising.callback_details.0.0.error eq "backend refused token <secret-hidden>" \
    --check raising.persisted_has_value eq false --check raising.persisted_has_mask eq true \
    --check policy.default_has_block eq true --check policy.default_has_builtin eq true --check policy.default_has_assessment eq true \
    --check policy.custom_verbatim eq true --check policy.custom_has_builtin eq false --check policy.custom_has_assessment eq true \
    --check policy.none_has_block eq false --check policy.none_has_builtin eq false --check policy.none_has_assessment eq true \
    --check policy.blank_has_block eq false --check policy.empty_file_has_builtin eq false --check policy.empty_file_has_assessment eq true
  ```
  `pause()` leaves a waiting conversation waiting. The SEVERE label never becomes an action; the agent gets the error and goes on. The glob call (read-only, labeled HIGH by the model) is rated UNKNOWN and so still stops under `ConfirmRisky()`. The custom policy text, `{{ raw }}` included, appears unrendered.
- **Risk levels, policies and every analyzer type deciding in a conversation (`S13.risk-levels`, `S13.policies`, `S13.pattern-analyzer`, `S13.policy-rails`, `S13.ensemble`, `S13.toolshield`, `S13.grayswan`, `S13.settings-security`).** `Ensemble(Pattern, PolicyRail)` with `ConfirmRisky(threshold=MEDIUM)` lets `ls` run and stops a fetch piped to `sh`. Under `AlwaysConfirm` a scripted agent then proposes 17 crafted commands (Unicode evasions, quoted, path-qualified and `sh -c` verbs, `dd`, `mkfs`, injection in the thought); each one waits and is rejected, so none runs. The recorded `ActionEvent`s are also rated by each analyzer alone. Next: ensemble fusion rules and a conversation persisted with the ensemble, then reopened. ToolShield with a scripted guardrail LLM. Gray Swan against a local stand-in (violation 0.1, 0.5, 0.9, 0.1 with `ipi`, HTTP 500, no score, a 2 s reply against a 0.5 s timeout). Finally `ConversationSettings(...).create_request(dict)` applied to a conversation.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S13/analyzers \
    --check risk.ordered eq true --check risk.high_is_riskier_than_low eq true --check risk.reflexive eq true \
    --check risk.non_reflexive eq false --check risk.unknown_compare eq ValueError --check risk.unknown_is_riskier eq ValueError \
    --check risk.max eq HIGH \
    --check policies.never.HIGH eq false --check policies.always.LOW eq true \
    --check policies.risky.HIGH eq true --check policies.risky.MEDIUM eq false --check policies.risky.UNKNOWN eq true \
    --check policies.risky_medium_no_unknown.MEDIUM eq true --check policies.risky_medium_no_unknown.UNKNOWN eq false \
    --check policies.risky_medium_no_unknown.LOW eq false \
    --check policies.risky_default_threshold eq HIGH --check policies.unknown_threshold eq ValidationError \
    --check policies.roundtrip.0 eq NeverConfirm --check policies.roundtrip.1 eq AlwaysConfirm \
    --check policies.roundtrip.3 eq ConfirmRisky --check policies.roundtrip_threshold eq MEDIUM \
    --check rules.stop eq waiting_for_confirmation --check rules.risk_at_stop eq HIGH --check rules.ls_ran eq true \
    --check rules.final eq finished --check rules.risks.0 eq LOW \
    --check classify.all_waited eq true --check classify.nothing_ran eq true --check classify.final eq finished \
    --check classify.pattern.safe eq LOW --check classify.pattern.rm_root eq HIGH --check classify.pattern.fullwidth eq HIGH \
    --check classify.pattern.zero_width eq HIGH --check classify.pattern.bidi eq HIGH --check classify.pattern.control_char eq HIGH \
    --check classify.pattern.quoted_verb eq HIGH --check classify.pattern.path_verb eq HIGH --check classify.pattern.nested_sh eq HIGH \
    --check classify.pattern.python_exec eq HIGH --check classify.pattern.injection_in_thought eq HIGH \
    --check classify.pattern.rm_only_in_thought eq LOW \
    --check classify.rail.safe eq LOW --check classify.rail.rm_root eq HIGH --check classify.rail.rm_home eq HIGH \
    --check classify.rail.quoted_verb eq HIGH --check classify.rail.path_verb eq HIGH --check classify.rail.nested_sh eq HIGH \
    --check classify.rail.fetch_pipe eq HIGH --check classify.rail.dd_disk eq HIGH --check classify.rail.mkfs eq HIGH \
    --check classify.rail.eval eq UNKNOWN --check classify.rail.injection_in_thought eq LOW --check classify.rail.rm_only_in_thought eq LOW \
    --check classify.ensemble.safe eq LOW --check classify.ensemble.fullwidth eq HIGH --check classify.ensemble.dd_disk eq HIGH \
    --check classify.ensemble.injection_in_thought eq HIGH --check classify.ensemble.rm_only_in_thought eq LOW \
    --check ensemble.max eq MEDIUM --check ensemble.fail_closed eq HIGH --check ensemble.unknown_ignored eq LOW \
    --check ensemble.all_unknown eq UNKNOWN --check ensemble.propagate_unknown eq UNKNOWN \
    --check ensemble.empty_rejected eq ValidationError \
    --check ensemble.dump_children.1 eq PolicyRailSecurityAnalyzer --check ensemble.roundtrip_children.0 eq PatternSecurityAnalyzer \
    --check ensemble.restored eq EnsembleSecurityAnalyzer --check ensemble.restored_children len-eq 2 \
    --check toolshield.risks.0 eq LOW --check toolshield.risks.1 eq HIGH --check toolshield.risks.2 eq UNKNOWN \
    --check toolshield.risks.3 eq UNKNOWN --check toolshield.risks.4 eq MEDIUM --check toolshield.risks.5 eq UNKNOWN \
    --check toolshield.guard_calls eq 6 --check toolshield.first_no_history eq true \
    --check toolshield.escaped_close_tag eq true --check toolshield.history_window_2 eq true \
    --check toolshield.system_prompt_has_labels eq true --check toolshield.zero_window_rejected eq ValidationError \
    --check toolshield.mcp_tools.0 eq terminal-mcp --check toolshield.mcp_tools.1 eq filesystem-mcp \
    --check toolshield.default_experiences eq ImportError \
    --check grayswan.no_key eq UNKNOWN --check grayswan.risks.0 eq LOW --check grayswan.risks.1 eq MEDIUM \
    --check grayswan.risks.2 eq HIGH --check grayswan.risks.3 eq HIGH --check grayswan.risks.4 eq UNKNOWN \
    --check grayswan.risks.5 eq UNKNOWN --check grayswan.risks.6 eq UNKNOWN \
    --check grayswan.auth eq "Bearer gs-key" --check grayswan.policy_id eq policy-from-env \
    --check grayswan.first_role eq system --check grayswan.action_in_request eq true \
    --check grayswan.history_has_user eq true --check grayswan.bad_thresholds eq ValidationError \
    --check settings.llm_policy eq ConfirmRisky --check settings.llm_analyzer eq LLMSecurityAnalyzer \
    --check settings.no_analyzer_policy eq AlwaysConfirm --check settings.no_analyzer_analyzer missing \
    --check settings.off_policy eq NeverConfirm --check settings.override eq AlwaysConfirm \
    --check settings.applied_status eq waiting_for_confirmation --check settings.applied_written eq false
  ```
  The guardrail replies are, in order: `RISK: LOW`; `RISK: HIGH`; a `RISK: LOW` only inside echoed `<arguments>` (UNKNOWN); `RISK: LOW` and `RISK: HIGH` together (UNKNOWN); a smuggled LOW plus its own `RISK: MEDIUM` (MEDIUM); a service error (UNKNOWN). `default_experiences` is `ImportError` because the optional `toolshield` extra is not installed.
- **Secrets from source to terminal to masked output (`S13.secret-sources`, `S13.secret-registry`, `S13.secret-injection`, `S13.mask-output`, `S13.mask-tool-output`, `S13.mask-models`).** The conversation gets `secrets={"API_TOKEN": ..., "LOOKED_UP": LookupSecret(...), "COUNTER": <own SecretSource>}`, `AgentContext(secrets={"API_TOKEN": "ctx-loses", "CTX_ONLY": ...})` and `update_secrets({"LATE_TOKEN": ..., "BROKEN": <500 lookup>})`. A scripted agent echoes them from the terminal, then views, greps and globs a file whose content and name hold a value, and ends with a message that quotes two values.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S13/secrets_run --arg sync \
    --check sources.static_value eq static-val --check sources.static_dump eq "**********" \
    --check sources.static_exposed eq static-val --check sources.lookup_value eq val-alpha \
    --check sources.lookup_auth_sent eq "Bearer svc-key" --check sources.lookup_dump_headers.Authorization eq "**********" \
    --check sources.lookup_dump_headers.X-Trace eq t1 --check sources.lookup_cipher_hides eq true \
    --check sources.lookup_cipher_restores eq "Bearer svc-key" --check sources.relative_url eq true \
    --check sources.relative_value eq val-relative --check sources.local_resolver_value eq local-val \
    --check sources.local_resolver_http_requests eq 0 --check sources.declined_falls_back eq val-other \
    --check sources.polymorphic eq LookupSecret \
    --check registry.find_case_insensitive len-eq 2 --check registry.env_for_command.LATE_TOKEN eq late-val \
    --check registry.env_for_command.BROKEN missing --check registry.env_without_reference len-eq 0 \
    --check registry.counter_calls_before_masking eq 0 --check registry.broken_hits_after_remask eq 0 \
    --check registry.overwrite eq late-val-2 --check registry.stream_split_value eq "start <secret-hidden> end" \
    --check registry.roundtrip_masks eq "x <secret-hidden> <secret-hidden> y" \
    --check run.status eq finished --check run.token_file eq tok-abc-s13 \
    --check run.others_file eq "val-beta ctx-only-val late-val" \
    --check run.terminal_0 contains "token=<secret-hidden>" --check run.terminal_0 not-contains tok-abc-s13 \
    --check run.terminal_1 contains "<secret-hidden> <secret-hidden> <secret-hidden>" \
    --check run.file_view_masked eq true --check run.file_view_leaks eq false \
    --check run.grep_masked eq true --check run.grep_leaks eq false --check run.glob_masked eq true --check run.glob_leaks eq false \
    --check run.agent_message eq "I found <secret-hidden> and <secret-hidden>." \
    --check run.prompt_lists len-eq 4 --check run.prompt_leaks eq false \
    --check run.persisted_leaks eq false --check run.persisted_masked eq true \
    --check models.title eq "t <secret-hidden>" --check models.inner_note eq "n <secret-hidden>" \
    --check models.inner_tags.0 eq "<secret-hidden>" --check models.items.0 eq "i <secret-hidden>" \
    --check models.mapping.k eq "v <secret-hidden>" --check models.pair.0 eq "p <secret-hidden>" \
    --check models.pair_is_namedtuple eq true --check models.count eq 7 --check models.key_untouched eq AAAAs13key \
    --check models.color_untouched eq true --check models.data_url_intact eq true \
    --check models.https_url eq "https://img.example/?k=<secret-hidden>" --check models.text eq "<secret-hidden>"
  ```
  `token.txt` holds the request's value, not the `AgentContext` one, so the request wins. The values reach the terminal, but every observation shows `<secret-hidden>`. The failing `BROKEN` lookup is skipped, and masking again inside the back-off window sends no new request.
- **Masking in `arun()` with a lookup the running loop serves (`S13.mask-async`).** The event loop that runs `arun()` also answers the `LookupSecret`, and while answering it registers `NESTED` on the conversation. In the first case the lookup first happens while an agent message is being masked; in the second, while a terminal observation is being masked.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S13/secrets_run --arg async \
    --check async.message_step.status eq finished \
    --check async.message_step.messages.1 eq "the values are <secret-hidden> and <secret-hidden>" \
    --check async.message_step.seconds lt 10 \
    --check async.tool_step.status eq finished \
    --check async.tool_step.messages.0 eq "the values are <secret-hidden> and <secret-hidden>" \
    --check async.tool_step.seconds lt 10
  ```
  Known failure (reproduced 2026-10-13, not yet filed): when the first lookup is triggered by masking a tool observation (`ToolDefinition.__call__` → `mask_secrets_in_model`), the state lock is still held during the HTTP request. The secrets service's `update_secrets` call therefore blocks until httpx's 30 s timeout, the lookup fails, and the back-off then leaves the value unmasked. Evidence: `async.tool_step.messages.0` is `"the values are loop-val and <secret-hidden>"` and `async.tool_step.seconds` is `30.5`, while the message-step case is masked and takes `0.1` s.
- **A real model labels its own actions (`S13.llm-analyzer`, `S13.confirm-wait`, `S13.confirm-approve-reject`).** Live. The model is asked to run `ls` and then `rm -rf scratch`; `ConfirmRisky()` stops, and the program rejects until the agent finishes.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk program S13/live_risk \
    --check status eq finished --check analyses ge 1 --check all_labeled eq true \
    --check stops ge 1 --check risks contains HIGH --check scratch_kept eq true
  ```
- **Custom security policy with a real model, example 32 (`S13.security-policy-prompt`).** Live.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output "Custom security policy successfully embedded" --expect-output "EXAMPLE_COST" -- python examples/01_standalone_sdk/32_configurable_security_policy.py
  ```
- **Defense-in-depth wiring, example 47 (`S13.ensemble`, `S13.policies`).** The example builds the ensemble and the policy but never calls the model, so the block runs without a key.
  ```sh
  # no model
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk exec --expect-output "EXAMPLE_COST: 0" -- python examples/01_standalone_sdk/47_defense_in_depth_security.py
  ```
- **Secrets in the terminal with a real model, example 12 (`S13.secret-injection`, `S13.mask-output`).** Live.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output "EXAMPLE_COST" -- python examples/01_standalone_sdk/12_custom_secrets.py
  ```
  The agent echoes `$SECRET_TOKEN` and `$SECRET_FUNCTION_TOKEN`; the observations show `<secret-hidden>`.
- **Interactive confirmation, examples 04 and 16 (`S13.confirm-approve-reject`, `S13.llm-analyzer`), blocked.** Both call `input()` for every pending batch and need a person at a terminal (`control-sdk exec` gives no stdin, and on EOF they reject everything). The scripted and live bullets above cover the same flow without the prompt.

## Gotchas

- Examples 04 and 16 are in `_EXCLUDED_EXAMPLES` (they call `input()`); on EOF they reject by default, so piping no input rejects every action.
- Example 16 runs with `persistence_dir="./.conversations"` and `workspace="."`, leaving state in the current directory.
- Example 47 only builds the ensemble and policy; it proves construction, not detection. Detection is proven by the defense-in-depth unit tests.
- Choosing an analyzer does not change the confirmation policy: without `set_confirmation_policy`, the default `NeverConfirm` only records `SecurityAnalysisEvent`s.
- With an analyzer but no risk label from the model, risk is `UNKNOWN`, and `ConfirmRisky()` asks because `confirm_unknown=True` by default.
- Without any analyzer, `security_risk` from the model is discarded and every action is `UNKNOWN`.
- 10 tests in `tests/sdk/security/defense_in_depth/` are strict `xfail`s for known bypasses (command substitution, ANSI-C quoting, homoglyphs, combining characters, payloads past the hard cap, thinking-block payloads); `test_shell_parser_bypasses.py` cites issue #2721. A strict xfail that starts passing fails the run.
- 7 ToolShield tests skip unless the optional `toolshield` extra is installed; this map does not install it.
- Masking only covers values the registry has resolved (tracked); a `LookupSecret` that was never fetched has nothing to mask yet.
- Example 12 overlaps the conversation family; it is listed here for the secret sources and masking it shows.
- Under `ConfirmRisky()`, read-only tools (glob, grep, browser reads) still stop: their `security_risk` is forced to UNKNOWN and `confirm_unknown=True` by default.
- `pause()` on a conversation in `WAITING_FOR_CONFIRMATION` changes nothing; only `run()` (approve) or `reject_pending_actions` moves it on.
- `ConversationSettings.security_analyzer` defaults to `"llm"`, so `confirmation_mode=True` alone gives `ConfirmRisky` + `LLMSecurityAnalyzer`; pass `security_analyzer="none"` for `AlwaysConfirm`. With `confirmation_mode=False` the LLM analyzer is still built and keeps recording `SecurityAnalysisEvent`s under `NeverConfirm`.
- `PolicyRailSecurityAnalyzer` does not normalize Unicode (fullwidth or zero-width `rm -rf /` rate LOW); only `PatternSecurityAnalyzer` does. Use the ensemble.
- Nothing in the SDK calls `GraySwanAnalyzer.set_events`. In a conversation each request carries only a synthesized system message plus the pending action, unless the program calls `set_events(...)` itself.
- Masking covers observations, agent messages and analysis events. An `ActionEvent`'s arguments and `thought` keep the literal value the model wrote, both in memory and in the persisted event file, in sync and async alike.
- Once a command references a secret, the variable stays exported in that terminal session: a later `env` shows it, although its output is masked.
- `sdk-map run` treats example 47 as model-backed because of its `examples/` path, although it never calls the model.
