# Agent settings, persisted schema migrations and agent profiles

An SDK user can describe an agent as data instead of code. `OpenHandsAgentSettings(llm=LLM(...), tools=[...], condenser=LLMSummarizingCondenserSettings(...), verification=VerificationSettings(...), mcp_config={...}, persona=..., agent_context=..., tool_concurrency_limit=...)` serializes to JSON, validates back, and `create_agent()` builds the `Agent` (tools defaulting to the standard set when left unset, a condenser only when enabled, a critic only when enabled and keyed). `ACPAgentSettings` does the same for an ACP-backed agent, resolving the server command from a provider registry. `validate_agent_settings(payload)` picks the variant from `agent_kind` and runs the migration chain on any persisted payload from `schema_version` 0 up to the current 8 (`ConversationSettings` has its own chain, currently 1), rejecting payloads newer than the code; `apply_agent_settings_diff(base, diff)` applies a JSON merge patch, switching variant when `agent_kind` changes; `export_agent_settings_schema()` emits the sectioned schema a settings UI renders. Golden payloads for every past schema version live in `tests/sdk/persisted_settings_baselines/vN/`, and a CI script proves the current code still loads them and the last PyPI release's payloads. On top of settings, an agent profile (`OpenHandsAgentProfile` / `ACPAgentProfile`, schema version 3) is a named, reference-bearing launch spec saved by `AgentProfileStore` under `~/.openhands/agent-profiles/<name>.json`; `resolve_agent_profile(...)` turns it into validated settings by loading the referenced LLM profile, filtering MCP servers and skills. `openhands.sdk.credential` provides `HttpVersionedCredentialBinding`, a versioned load/replace client for file credentials that ACP agents sync with a credential source.

Source: `openhands-sdk/openhands/sdk/settings/`, `openhands-sdk/openhands/sdk/conversation/request.py`, `openhands-sdk/openhands/sdk/conversation/message_request.py`, `openhands-sdk/openhands/sdk/profiles/`, `openhands-sdk/openhands/sdk/credential.py`, `.github/scripts/check_persisted_settings_compat.py`, `tests/sdk/persisted_settings_baselines/`

Needs: `llm`, `tmux`, `network`

Examples: `examples/01_standalone_sdk/46_agent_settings.py`

## Sub-features

- `S19.settings-create-agent`: `OpenHandsAgentSettings(...).create_agent()` returns an `Agent` with the settings' LLM, tools, MCP config and agent context. Unit: `tests/sdk/test_settings.py::test_llm_create_agent_uses_settings_llm_and_tools`. Example: `examples/01_standalone_sdk/46_agent_settings.py`. Real-LLM: none.
- `S19.settings-roundtrip`: settings dump to JSON and `model_validate` restores them, preserving the LLM model, condenser fields, disabled MCP servers and secrets held by a validated instance. Unit: `tests/sdk/test_settings.py::test_llm_roundtrip_preserves_llm_model`, `::test_disabled_mcp_server_survives_settings_round_trip`. Example: `examples/01_standalone_sdk/46_agent_settings.py`. Real-LLM: none.
- `S19.settings-tools`: `tools=None` gives the standard tool set, `tools=[]` gives a bare agent, duplicates collapse, built-ins are matched by class name and attached once, and a registered tool named like a built-in is not replaced. Unit: `tests/sdk/test_settings.py::test_create_agent_defaults_tools_when_none`, `::test_create_agent_empty_tools_stays_bare`, `::test_create_agent_collapses_duplicate_tools`, `::test_builtin_named_twice_is_attached_once`. Example: `examples/01_standalone_sdk/46_agent_settings.py` (terminal-only variant). Real-LLM: none.
- `S19.retired-switches`: the retired booleans `enable_sub_agents` and `enable_switch_llm_tool` (`RETIRED_TOOL_SWITCHES` in `openhands-sdk/openhands/sdk/tool/defaults.py`) fold into the `tools` list on load and in diffs, pin the same set the v6 migration pins (with the browser pinned only for loaded settings), and are never serialized again. Unit: `tests/sdk/test_settings.py::test_retired_tool_switches_are_not_serialized`, `::test_v6_settings_fold_the_retired_switches`, `::test_live_retired_switch_pins_the_same_set_as_the_v6_migration`. Example: none. Real-LLM: none.
- `S19.condenser-settings`: `LLMSummarizingCondenserSettings(enabled=True, max_size=...)` builds an `LLMSummarizingCondenser` whose fields match the condenser's, inheriting `max_tokens` from the LLM unless set; `enabled=False` gives no condenser; `NoOpCondenserSettings` builds a `NoOpCondenser`; a legacy condenser payload defaults to the summarizing variant. Unit: `tests/sdk/test_settings.py::test_llm_create_agent_builds_condenser_when_enabled`, `::test_llm_summarizing_condenser_settings_match_condenser_fields`, `::test_llm_create_agent_builds_no_op_condenser_variant`, `::test_llm_create_agent_no_condenser_when_disabled`. Example: `examples/01_standalone_sdk/46_agent_settings.py`. Real-LLM: none.
- `S19.verification-settings`: `VerificationSettings(critic_enabled=True, ...)` builds a critic (with iterative refinement when configured) only when a critic API key or the LLM key is available; the key round-trips. Unit: `tests/sdk/test_settings.py::test_llm_create_agent_builds_critic_when_enabled`, `::test_llm_create_agent_no_critic_without_api_key`, `::test_llm_create_agent_critic_with_iterative_refinement`. Example: none. Real-LLM: none.
- `S19.mcp-config-secrets`: `mcp_config` is a typed server map; env, headers and bearer auth are redacted in dumps, encrypted with a cipher when one is supplied, legacy plaintext decrypts, and `create_agent()` gets the real values. Unit: `tests/sdk/test_settings.py::test_openhands_agent_settings_mcp_config_redacts_env_and_headers`, `::test_mcp_config_encrypts_env_and_headers_with_cipher`, `::test_openhands_agent_settings_create_agent_keeps_real_mcp_secrets`. Example: none. Real-LLM: none.
- `S19.tool-concurrency-persona`: `tool_concurrency_limit` defaults to 1 and rejects invalid values; `persona` is applied to the built agent's prompt, and without it the built-in prompt is used. Unit: `tests/sdk/test_settings.py::test_tool_concurrency_limit_invalid_values_rejected`, `::test_create_agent_applies_persona`. Example: none. Real-LLM: none.
- `S19.subscription-llm-settings`: subscription-auth LLM fields round-trip through settings and are rehydrated into a working runtime LLM on load. Unit: `tests/sdk/test_settings.py::test_llm_subscription_fields_roundtrip`, `::test_llm_from_persisted_rehydrates_subscription_runtime`. Example: none. Real-LLM: none.
- `S19.acp-settings`: `ACPAgentSettings(acp_server=...)` resolves the default command for known servers (rewriting `npx` to a pinned binary when present), requires an explicit command for a custom server, ignores and warns about an `llm`, and maps the provider's API-key env var. Unit: `tests/sdk/test_settings.py::test_acp_create_agent_uses_server_default_command`, `::test_acp_custom_server_requires_explicit_command`, `::test_acp_settings_llm_warns_and_is_ignored`. Example: none. Real-LLM: none.
- `S19.acp-providers-catalog`: the ACP provider registry (`ACP_PROVIDERS`, detection by agent name or command, file-secret specs) and the install catalog of pinned npm packages with a rendered Docker install plan. Unit: `tests/sdk/settings/test_acp_providers.py`, `tests/sdk/settings/test_acp_install_catalog.py`. Example: none. Real-LLM: none.
- `S19.validate-dispatch`: `validate_agent_settings` dispatches on `agent_kind` (`openhands` or `acp`), defaults to `openhands` when it is missing, and canonicalizes the legacy `llm` kind so it never returns `LLMAgentSettings`. Unit: `tests/sdk/test_settings.py::test_validate_agent_settings_dispatches_on_agent_kind`, `::test_validate_agent_settings_defaults_to_openhands_when_discriminator_missing`, `tests/sdk/test_apply_agent_settings_diff.py::test_validate_never_returns_llm_subclass`. Example: none. Real-LLM: none.
- `S19.schema-migrations`: persisted agent settings from v0 to v7 migrate step by step to v8 (legacy proxy LLM, `modify_params`, MCP auth shapes and `type` to `transport`, ACP LLM removal, retired switches); a newer, negative or non-integer `schema_version` is rejected; `from_persisted` and `model_validate` agree on current payloads. Unit: `tests/sdk/test_settings.py::test_validate_agent_settings_migrates_v0_llm_payload`, `::test_validate_agent_settings_rejects_newer_schema_version`, `::test_openhands_agent_settings_from_persisted_migrates_legacy_payloads`. Example: none. Real-LLM: none.
- `S19.conversation-settings`: `ConversationSettings` (schema v1) round-trips, migrates a v0 payload, rejects newer versions, validates observability metadata, accepts both agent variants, and `create_request(...)` builds a start-conversation request with confirmation policy and security analyzer. Unit: `tests/sdk/test_settings.py::test_conversation_settings_model_dump_roundtrip`, `::test_conversation_settings_from_persisted_migrates_v0_payload`, `::test_conversation_settings_create_request`. Example: none. Real-LLM: none.
- `S19.apply-diff`: `apply_agent_settings_diff(base, diff)` deep-merges within a variant, treats `None` as unset (merge patch), replaces the base with a fresh variant when `agent_kind` switches (dropping `persona` for ACP), and keeps secrets from the base. Unit: `tests/sdk/test_apply_agent_settings_diff.py`. Example: none. Real-LLM: none.
- `S19.schema-export`: `export_agent_settings_schema()` and `export_settings_schema(Model)` emit variant-tagged sections with field metadata (label, prominence), stay backward compatible, and work on both variants. Unit: `tests/sdk/test_settings.py::test_export_agent_settings_schema_emits_variant_tagged_sections`, `::test_export_settings_schema_remains_backward_compatible`, `::test_llm_agent_settings_export_schema_groups_sections`. Example: none. Real-LLM: none.
- `S19.persisted-baselines`: every `tests/sdk/persisted_settings_baselines/vN/*.json` (agent settings, agent profiles, conversation settings, agent-server persisted settings) loads with the current code and keeps its sentinel values; a shape change without a version bump, or a removed profile field, is rejected; every schema version must have a fixture. Unit: `tests/cross/test_check_persisted_settings_compat.py`. Example: none. Real-LLM: none.
- `S19.pypi-baseline-gate`: the CI script generates payloads with the previous PyPI release of `openhands-sdk` and `openhands-agent-server` (via `uv`, with a release-date cutoff) and validates them against the current code. Unit: `tests/cross/test_check_persisted_settings_compat.py::test_generate_baseline_payloads_uses_uv_with_release_cutoff`, `::test_get_pypi_baseline_version_prefers_current_or_previous` (mocked). Example: none. Real-LLM: none.
- `S19.profile-model`: `OpenHandsAgentProfile` requires `llm_profile_ref`, `ACPAgentProfile` requires a known ACP server and rejects LLM refs and persona; both carry a UUID `id`, `name`, `revision`, `mcp_server_refs` (null means all, `[]` means none), `secret_refs`, and never persist secrets. Unit: `tests/sdk/profiles/test_agent_profile.py`, `tests/sdk/profiles/test_secret_refs.py`. Example: none. Real-LLM: none.
- `S19.profile-migrations`: profiles at schema v1 and v2 migrate to v3 (empty or default tools, embedded skills to `disabled_skills`, v2 switches to tools); newer, negative or non-integer versions are rejected. Unit: `tests/sdk/profiles/test_agent_profile.py::test_v1_untouched_default_migrates_empty_tools_to_null`, `::test_rejects_newer_schema_version`. Example: none. Real-LLM: none.
- `S19.profile-store`: `AgentProfileStore(base_dir=None)` saves atomically with the schema version and id, lists, loads (showing available names on a miss), renames (keeping the id), deletes, lists summaries skipping corrupted files, enforces `max_profiles`, and is safe under concurrent access through a file lock. Unit: `tests/sdk/profiles/test_agent_profile_store.py`. Example: none. Real-LLM: none.
- `S19.profile-resolve`: `resolve_agent_profile(profile, llm_store=..., mcp_config=..., available_skills=..., browser_available=...)` builds validated settings: loads the LLM profile (raising `ProfileNotFound`), filters MCP servers (raising `DanglingMcpServerRef`), applies `disabled_skills`, scopes tools and delegation, and keeps persona and verification; `resolve_agent_profile_dry_run` reports the same verdict with secrets redacted. Unit: `tests/sdk/profiles/test_resolver.py`. Example: none. Real-LLM: none.
- `S19.profile-refs`: renaming an LLM profile cascades to every agent profile that cites it, atomically; deleting a referenced LLM profile raises `ProfileReferenced` naming the referrers. Unit: `tests/sdk/profiles/test_profile_refs.py`, `tests/sdk/profiles/test_store_protocol_hoists.py`. Example: none. Real-LLM: none.
- `S19.profile-seed`: `build_seed_profile(settings)` derives the default profile from current settings (persona, explicit tools, ACP branch, no disabled skills); `save_profile_preserving_identity` keeps the id and bumps `revision`. Unit: `tests/sdk/profiles/test_store_protocol_hoists.py`. Example: none. Real-LLM: none.
- `S19.credential-binding`: `HttpVersionedCredentialBinding(url, headers).load()` GETs `{value, version}`, `replace(expected_version, value)` PUTs and returns the new version; 404 and 400/422 raise `CredentialNeedsReauthentication`, 409 `CredentialConflict`, 401/403 `CredentialAuthorizationRejected`, 501 `CredentialBindingUnsupported`, invalid URLs `CredentialInvalidResponse`; `reauthorize()` swaps headers for the same URL. Unit: `tests/sdk/test_credential.py`. Example: none. Real-LLM: none.

## How to get to it (agent POV)

- `from openhands.sdk import OpenHandsAgentSettings`; `from openhands.sdk.settings import LLMSummarizingCondenserSettings, NoOpCondenserSettings, VerificationSettings, ACPAgentSettings, ConversationSettings, validate_agent_settings, apply_agent_settings_diff, default_agent_settings, create_agent_from_settings, export_agent_settings_schema, export_settings_schema`: `S19.settings-*`, `S19.condenser-settings`, `S19.verification-settings`, `S19.acp-settings`, `S19.validate-dispatch`, `S19.apply-diff`, `S19.schema-export`, `S19.conversation-settings`.
- `settings.create_agent()`, `settings.model_dump(mode="json")`, `OpenHandsAgentSettings.model_validate(payload)`, `OpenHandsAgentSettings.from_persisted(payload)`, `ConversationSettings.from_persisted(payload)`: `S19.settings-roundtrip`, `S19.schema-migrations`.
- Constants: `AGENT_SETTINGS_SCHEMA_VERSION = 8`, `CONVERSATION_SETTINGS_SCHEMA_VERSION = 1` (`settings/model.py`), `AGENT_PROFILE_SCHEMA_VERSION = 3` (`profiles/agent_profile.py`).
- Files: `tests/sdk/persisted_settings_baselines/v1..v8/*.json`, `.github/scripts/check_persisted_settings_compat.py`, workflow `.github/workflows/persisted-settings-compat.yml`: `S19.persisted-baselines`, `S19.pypi-baseline-gate`.
- `from openhands.sdk.profiles import AgentProfileStore, OpenHandsAgentProfile, ACPAgentProfile, validate_agent_profile, resolve_agent_profile, resolve_agent_profile_dry_run, cascade_rename, delete_llm_profile, rename_llm_profile, build_seed_profile, save_profile_preserving_identity`; on disk `~/.openhands/agent-profiles/<name>.json` (moved by `OH_PERSISTENCE_DIR`): `S19.profile-*`. LLM profiles themselves (the `llm_profile_ref` targets) belong to the LLM profile store family (example 37).
- `from openhands.sdk.credential import HttpVersionedCredentialBinding, ResolvedCredential, Credential*`: `S19.credential-binding`; consumed by the ACP agent's file-credential sync.
- Env read by example 46: `LLM_API_KEY`, `LLM_MODEL` (default `gpt-5.5`), `LLM_BASE_URL`.

## Driving it

Preconditions:
- `uv sync --dev` has run; `control-sdk` is on `PATH` (`export PATH="$PWD/.agents/skills/verify-sdk/scripts:$PATH"`). `sdk-map run` does both.
- `tmux` on `PATH` (the settings-built agents run the terminal tool).
- The npm-pin and PyPI-gate bullets need outbound network to registry.npmjs.org and pypi.org (they exit 3, `blocked`, when the registry is unreachable); live bullets need `DEEPSEEK_API_KEY`.
- Profile and LLM-profile stores live under the run's private `OH_PERSISTENCE_DIR`, never the caller's `~/.openhands`.

- **Settings build a working agent (`S19.settings-create-agent`, `S19.settings-roundtrip`, `S19.settings-tools`, `S19.retired-switches`, `S19.condenser-settings`, `S19.verification-settings`, `S19.mcp-config-secrets`, `S19.tool-concurrency-persona`).** `OpenHandsAgentSettings` with tools, MCP servers, a condenser, a critic and a persona; saved as JSON, reloaded, built with `create_agent()`; then the built agent (its LLM swapped for a scripted one) runs the terminal in a conversation.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S19/agent_settings \
    --check create_agent.type eq Agent --check create_agent.model eq openai/settings-model \
    --check create_agent.tools len-eq 2 --check create_agent.mcp_servers len-eq 3 --check create_agent.suffix eq "Answer tersely." \
    --check roundtrip.schema_version eq 8 --check roundtrip.model eq openai/settings-model \
    --check roundtrip.condenser_max_size eq 50 --check roundtrip.condenser_keep_first eq 3 \
    --check roundtrip.off_server_enabled eq false --check roundtrip.off_server_command eq uvx \
    --check roundtrip.llm_key_in_plain_dump eq false --check roundtrip.from_persisted_equal eq true \
    --check tools.default contains terminal --check tools.default contains file_editor --check tools.default contains task_tracker \
    --check tools.bare_count eq 0 --check tools.bare not-contains terminal --check tools.dup_terminal_count eq 1 \
    --check retired_switches.same_set eq true --check retired_switches.live_tools contains task_tool_set \
    --check retired_switches.live_tools contains browser_tool_set --check retired_switches.in_code_tools not-contains browser_tool_set \
    --check retired_switches.serialized_keys len-eq 0 \
    --check condenser.on_type eq LLMSummarizingCondenser --check condenser.on_max_size eq 80 \
    --check condenser.on_max_tokens eq 32000 --check condenser.explicit_max_tokens eq 5000 \
    --check condenser.off missing --check condenser.noop_type eq NoOpCondenser \
    --check condenser.legacy_type eq LLMSummarizingCondenserSettings \
    --check verification.with_llm_key eq APIBasedCritic --check verification.without_key missing \
    --check verification.disabled missing --check verification.refinement_threshold eq 0.7 \
    --check verification.refinement_max eq 2 --check verification.key_roundtrip eq true \
    --check mcp_secrets.redacted_in_dump eq true --check mcp_secrets.encrypted_differs eq true \
    --check mcp_secrets.decrypted eq ghp-secret --check mcp_secrets.decrypted_header eq tok-secret \
    --check mcp_secrets.legacy_plaintext eq ghp-secret --check mcp_secrets.agent_real_env eq ghp-secret \
    --check concurrency_persona.default_limit eq 1 --check concurrency_persona.rejected len-eq 3 \
    --check concurrency_persona.four eq 4 --check concurrency_persona.persona_in_prompt eq true \
    --check concurrency_persona.builtin_in_plain_prompt eq true \
    --check conversation.status eq finished --check conversation.file eq built-from-settings \
    --check conversation.persona_in_system_prompt eq true --check conversation.tool_names contains terminal
  ```
  `tools=None` gives the standard set, `tools=[]` only the built-in finish and think tools; a live `enable_sub_agents` diff and a v6 payload pin the same tool list (browser included, as for loaded settings), while settings built in code with the switch do not add the browser. The condenser inherits the LLM's `max_input_tokens` unless `max_tokens` is set. MCP `env`/headers are redacted in a plain dump, encrypted with a cipher, and the built agent gets the real values.
- **Loading, migrating, diffing and describing settings (`S19.validate-dispatch`, `S19.schema-migrations`, `S19.conversation-settings`, `S19.apply-diff`, `S19.schema-export`, `S19.subscription-llm-settings`, `S19.acp-settings`).** The paths a settings store and a settings UI take.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S19/agent_settings \
    --check dispatch.openhands eq OpenHandsAgentSettings --check dispatch.acp eq ACPAgentSettings \
    --check dispatch.missing_kind eq OpenHandsAgentSettings --check dispatch.legacy_llm_kind eq OpenHandsAgentSettings \
    --check dispatch.legacy_is_llm_subclass eq false \
    --check migrations.current_version eq 8 --check migrations.v0_dump_version eq 8 \
    --check migrations.v0_mcp_server_map contains fetch \
    --check migrations.rejected.9 eq true --check migrations.rejected.-1 eq true --check migrations.rejected.seven eq true \
    --check migrations.persisted_equals_validate eq true \
    --check conversation_settings.schema_version eq 1 --check conversation_settings.roundtrip eq true \
    --check conversation_settings.v0_max_iterations eq 12 --check conversation_settings.newer_rejected exists \
    --check conversation_settings.request_max_iterations eq 77 --check conversation_settings.request_policy eq ConfirmRisky \
    --check conversation_settings.request_analyzer eq LLMSecurityAnalyzer \
    --check conversation_settings.override_max_iterations eq 5 --check conversation_settings.override_policy eq AlwaysConfirm \
    --check conversation_settings.override_analyzer missing \
    --check apply_diff.merged_max_size eq 90 --check apply_diff.merged_enabled_kept eq true \
    --check apply_diff.merged_model eq openai/new --check apply_diff.merged_key_kept eq true \
    --check apply_diff.persona_unset missing --check apply_diff.switched_type eq ACPAgentSettings \
    --check apply_diff.switched_has_persona eq false \
    --check schema.variants contains acp --check schema.variants contains openhands --check schema.labels eq true \
    --check schema.prominence_values contains critical --check schema.conversation_sections ge 1 \
    --check subscription.auth_type eq subscription --check subscription.vendor eq openai \
    --check subscription.dumped_auth_type eq subscription --check subscription.api_key_in_dump eq false \
    --check acp.is_acp eq true --check acp.command contains "@agentclientprotocol/claude-agent-acp@0.63.0" \
    --check acp.model eq claude-opus-4-6 --check acp.server eq claude-code --check acp.api_key_env eq ANTHROPIC_API_KEY \
    --check acp.custom_without_command eq ValueError --check acp.custom_command contains my_acp.py \
    --check acp.llm_warned eq true --check acp.llm_dropped eq true
  ```
  The `npx` default command applies because `claude-agent-acp` is not on `PATH` here (`acp.pinned_binary_on_path`); with the pinned binary installed it is rewritten to that binary.
- **Subscription LLM rehydration (`S19.subscription-llm-settings`), blocked: needs a ChatGPT subscription login (`LLM.subscription_login()`, interactive OAuth).** The round trip of the subscription fields is proven above; turning them into a working runtime LLM needs the stored OAuth credentials.
- **ACP provider registry and install catalog (`S19.acp-providers-catalog`).** The registry, detection by agent name and by command, file secrets, and the install plan, through the program and through the catalog's own CLI (the agent-server Dockerfile's `acp-providers` stage).
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S19/acp_catalog \
    --check providers len-eq 6 --check commands_pin_catalog.claude-code eq true --check commands_pin_catalog.codex eq true \
    --check commands_pin_catalog.pi eq true --check commands_pin_catalog.opencode eq true \
    --check by_agent_name.claude-agent-acp eq claude-code --check by_agent_name.Codex-ACP eq codex \
    --check by_agent_name.my-own-agent missing \
    --check by_command.npx_codex eq codex --check by_command.bin_claude eq claude-code \
    --check by_command.wrapper missing --check by_command.custom missing \
    --check file_secrets.codex contains CODEX_AUTH_JSON --check file_secrets.pi contains PI_AUTH_JSON \
    --check default_file_secrets len-eq 4 --check unknown_provider missing \
    --check default_plan.bins len-eq 3 --check pi_packages_deduplicated eq true --check unknown_plan_error contains "expected one of"
  control-sdk exec --expect-output 'PACKAGES="@agentclientprotocol/claude-agent-acp@0.63.0 @agentclientprotocol/codex-acp@1.10.0"' \
    --expect-output 'WRAPPER_BINS="claude-agent-acp codex-acp"' -- python -m openhands.sdk.settings.acp_install_catalog claude-code,codex
  ```
- **Pinned ACP packages are published (`S19.acp-providers-catalog`).** Network: every pin in the install catalog exists on registry.npmjs.org (exit 3 when the registry cannot be reached).
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S19/acp_catalog --arg npm --check pins ge 7 --check missing_pins len-eq 0
  ```
- **Persisted baselines (`S19.persisted-baselines`).** Every golden payload under `tests/sdk/persisted_settings_baselines/` loads through its public loader (`validate_agent_settings`, `validate_agent_profile`, `ConversationSettings.from_persisted`, the agent server's `PersistedSettings.from_persisted`) with its sentinel values; a set missing the newest schema version, or a fixture whose sentinel no longer survives, is refused.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S19/persisted_baselines \
    --check fixtures ge 23 --check versions len-eq 8 --check loaded.agent_settings ge 12 --check loaded.agent_profile ge 5 \
    --check loaded.conversation_settings ge 1 --check load_failures len-eq 0 --check gate_error missing \
    --check missing_version_refused eq true --check sentinel_refused eq PersistedSettingsCompatError
  ```
- **The PyPI baseline gate (`S19.pypi-baseline-gate`, `S19.persisted-baselines`).** Network: the CI script validates the fixtures, then generates payloads with the previous PyPI release of `openhands-sdk` and `openhands-agent-server` (through `uv` with a release-date cutoff) and validates them against the current code. Offline the second half fails.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk exec --timeout 900 --expect-output "persisted settings fixture" \
    --expect-output "from PyPI release openhands-sdk==" -- python .github/scripts/check_persisted_settings_compat.py
  ```
- **Agent profiles (`S19.profile-model`, `S19.profile-migrations`, `S19.profile-store`, `S19.profile-resolve`, `S19.profile-refs`, `S19.profile-seed`).** A launcher's path: an LLM profile `fast` and agent profiles are saved in the run's store, one is resolved against the user's MCP servers and skills and launched (scripted), and the LLM profile is renamed and (refused) deleted.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S19/agent_profiles \
    --check schema_version eq 3 --check model.id_is_uuid eq true --check model.mcp_refs_default missing \
    --check model.errors.missing_llm_ref eq ValidationError --check model.errors.acp_unknown_server eq ValidationError \
    --check model.errors.acp_with_llm_ref eq ValidationError --check model.errors.acp_with_persona eq ValidationError \
    --check model.dump_keys_have_secret eq false \
    --check migrations.v1_version eq 3 --check migrations.v1_tools missing --check migrations.v2_tools contains task_tool_set \
    --check migrations.rejected.4 eq true --check migrations.rejected.-1 eq true --check migrations.rejected.three eq true \
    --check store.file_in_persistence_dir eq true --check store.file_schema_version eq 3 --check store.same_id eq true \
    --check store.loaded_persona eq Pirate --check store.missing_names_available eq true \
    --check store.rename_kept_id eq true --check store.renamed_name eq qa \
    --check store.summaries len-eq 2 --check store.limit eq ProfileLimitExceeded --check store.race_revision eq 5 \
    --check store.list_after_delete not-contains qa.json \
    --check resolve.type eq OpenHandsAgentSettings --check resolve.model eq openai/fast-model --check resolve.llm_key_loaded eq true \
    --check resolve.mcp len-eq 1 --check resolve.all_mcp len-eq 2 --check resolve.none_mcp len-eq 0 \
    --check resolve.skills len-eq 1 --check resolve.skills contains deploy --check resolve.persona eq Pirate \
    --check resolve.errors.missing_llm eq ProfileNotFound --check resolve.errors.dangling_mcp eq DanglingMcpServerRef \
    --check resolve.dry_dump.valid eq false --check resolve.dry_dump.dangling_mcp_server_refs contains gone \
    --check resolve.dry_has_secret eq false --check resolve.launched_status eq finished \
    --check refs.referrers contains a1 --check refs.delete_blocked_names_referrers eq true \
    --check refs.rewritten contains a2 --check refs.a1_ref eq quick --check refs.llm_profiles contains quick.json \
    --check seed.name eq default --check seed.llm_ref eq quick --check seed.persona eq Pirate \
    --check seed.mcp_refs missing --check seed.disabled_skills len-eq 0 --check seed.acp_type eq ACPAgentProfile \
    --check seed.id_kept eq true --check seed.revision_bumped eq true
  ```
  Six threads saving the same name through `save_profile_preserving_identity` leave one profile, one id, revision 5. `list()` returns file names (`coder.json`), as documented.
- **Credential binding (`S19.credential-binding`).** A versioned credential source served on localhost (Bearer auth, 409 on a stale version, fixed status paths), loaded and replaced through `HttpVersionedCredentialBinding`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S19/credential_binding \
    --check load.value eq auth-v1 --check load.version eq 1 --check replace_version eq 2 --check after_replace eq auth-v2 \
    --check stale eq CredentialConflict --check bad_auth eq CredentialAuthorizationRejected \
    --check reauthorized_value eq auth-v2 --check revision_bumped eq true \
    --check reauthorize_other_url eq credential_binding_url_mismatch \
    --check status_errors.404 eq CredentialNeedsReauthentication --check status_errors.400 eq CredentialNeedsReauthentication \
    --check status_errors.422 eq CredentialNeedsReauthentication --check status_errors.409 eq CredentialConflict \
    --check status_errors.401 eq CredentialAuthorizationRejected --check status_errors.403 eq CredentialAuthorizationRejected \
    --check status_errors.501 eq CredentialBindingUnsupported --check status_errors.500 eq CredentialSyncError \
    --check invalid_url eq CredentialInvalidResponse --check garbage eq CredentialInvalidResponse \
    --check unreachable eq CredentialSyncError --check server_puts eq 1
  ```
- **Example 46, live (`S19.settings-create-agent`, `S19.settings-roundtrip`, `S19.settings-tools`, `S19.condenser-settings`).** Settings with a real model: the example round-trips the settings JSON, runs a conversation that writes `hello_settings.txt`, and builds a terminal-only agent without a condenser.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output "Roundtrip deserialization successful" \
    --expect-output "Different settings produce different agent configuration" --expect-output EXAMPLE_COST \
    -- python examples/01_standalone_sdk/46_agent_settings.py
  ```

## Gotchas

- `pyproject.toml` `addopts` deselects `stress`, `acp_live` and `docker_live` tests; `uv run pytest tests/sdk/settings -q` reports `86 passed, 13 deselected`, which is expected, not a failure.
- A persisted shape change needs three things together (see `AGENTS.md`): bump the schema version constant, add the migration step, and add `tests/sdk/persisted_settings_baselines/vN/` fixtures for the old shape. `test_validate_fixture_cases_requires_every_schema_version` fails if a version has no fixture.
- `check_persisted_settings_compat.py` fetches `https://pypi.org/pypi/openhands-sdk/json` and runs `uv` with `--exclude-newer`; without network it fails in the PyPI half even though the fixture half passed. If no published baseline exists it prints a `::warning` and exits 0.
- `LLMAgentSettings` is a deprecated alias kept importable; `validate_agent_settings` never returns it. Code that checks `type(settings) is LLMAgentSettings` breaks.
- MCP secrets are redacted in `model_dump()` unless a cipher or an expose context is given; a dumped-then-reloaded settings object without a cipher has lost the real values (`test_openhands_agent_settings_mcp_config_expose_encrypted_requires_cipher`).
- `_DEFAULT_PROFILE_DIR` is computed at import time from `OH_PERSISTENCE_DIR`; set it before importing `openhands.sdk`, or pass `AgentProfileStore(base_dir=...)`.
- `ACPProviderInfo.available_models` is deprecated (removal in 1.59.0); its tests emit `DeprecatedWarning`, which is expected.
- No failing tests were found in this family's commands at 4cc0582.
