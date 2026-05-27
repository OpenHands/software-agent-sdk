import json
import warnings

import pytest
from fastmcp.mcp_config import MCPConfig
from pydantic import SecretStr

from openhands.agent_server.models import StartConversationRequest
from openhands.sdk import (
    LLM,
    ACPAgentSettings,
    Agent,
    AgentContext,
    AgentSettingsBase,
    ConversationSettings,
    OpenHandsAgentSettings,
    SettingProminence,
    Tool,
    default_agent_settings,
    export_agent_settings_schema,
    validate_agent_settings,
)
from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.critic.base import IterativeRefinementConfig
from openhands.sdk.critic.impl.api import APIBasedCritic
from openhands.sdk.security.confirmation_policy import AlwaysConfirm, ConfirmRisky
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer
from openhands.sdk.settings import (
    AGENT_SETTINGS_SCHEMA_VERSION,
    CondenserSettings,
    VerificationSettings,
)
from openhands.sdk.workspace import LocalWorkspace


# Fields on LLM that have ``exclude=True`` and should not appear in the schema.
_LLM_EXCLUDED_FIELDS = {name for name, fi in LLM.model_fields.items() if fi.exclude}


# ---------------------------------------------------------------------------
# Schema export — per-variant
# ---------------------------------------------------------------------------


def test_llm_agent_settings_export_schema_groups_sections() -> None:
    schema = OpenHandsAgentSettings.export_schema()

    assert schema.model_name == "OpenHandsAgentSettings"
    section_keys = [section.key for section in schema.sections]
    assert section_keys == [
        "general",
        "llm",
        "condenser",
        "verification",
    ]

    sections = {s.key: s for s in schema.sections}

    # -- general section (top-level scalar fields) --
    general_fields = {f.key: f for f in sections["general"].fields}
    assert set(general_fields) == {
        "agent",
        "tools",
        "enable_sub_agents",
        "enable_switch_llm_tool",
        "mcp_config",
    }
    assert general_fields["agent"].default == "CodeActAgent"
    assert general_fields["agent"].prominence is SettingProminence.MAJOR
    assert general_fields["tools"].value_type == "array"
    assert general_fields["tools"].default == []
    assert general_fields["tools"].prominence is SettingProminence.MAJOR
    assert general_fields["enable_sub_agents"].value_type == "boolean"
    assert general_fields["enable_sub_agents"].default is False
    assert general_fields["enable_sub_agents"].prominence is SettingProminence.MAJOR
    assert general_fields["enable_switch_llm_tool"].value_type == "boolean"
    assert general_fields["enable_switch_llm_tool"].default is True
    assert (
        general_fields["enable_switch_llm_tool"].prominence is SettingProminence.MINOR
    )

    # -- llm section --
    llm_fields = {f.key: f for f in sections["llm"].fields}
    expected_llm_keys = {
        f"llm.{name}" for name in LLM.model_fields if name not in _LLM_EXCLUDED_FIELDS
    }
    assert set(llm_fields) == expected_llm_keys

    assert llm_fields["llm.model"].value_type == "string"
    assert llm_fields["llm.model"].prominence is SettingProminence.CRITICAL
    assert llm_fields["llm.max_input_tokens"].default is None
    assert llm_fields["llm.max_output_tokens"].default is None
    assert llm_fields["llm.api_key"].label == "API Key"
    assert llm_fields["llm.api_key"].secret is True
    assert llm_fields["llm.api_key"].prominence is SettingProminence.CRITICAL
    assert llm_fields["llm.base_url"].prominence is SettingProminence.MAJOR

    # Excluded fields must not appear
    assert "llm.fallback_strategy" not in llm_fields
    assert "llm.retry_listener" not in llm_fields

    # -- condenser section --
    condenser_fields = {f.key: f for f in sections["condenser"].fields}
    assert (
        condenser_fields["condenser.enabled"].prominence is SettingProminence.CRITICAL
    )
    assert condenser_fields["condenser.max_size"].depends_on == ["condenser.enabled"]
    assert condenser_fields["condenser.max_size"].prominence is SettingProminence.MINOR

    # -- verification section (critic settings only) --
    v_fields = {f.key: f for f in sections["verification"].fields}
    assert v_fields["verification.critic_mode"].value_type == "string"
    assert [c.value for c in v_fields["verification.critic_mode"].choices] == [
        "finish_and_message",
        "all_actions",
    ]
    assert (
        v_fields["verification.enable_iterative_refinement"].prominence
        is SettingProminence.CRITICAL
    )


def test_acp_agent_settings_export_schema_has_acp_section() -> None:
    schema = ACPAgentSettings.export_schema()
    assert schema.model_name == "ACPAgentSettings"

    section_keys = [section.key for section in schema.sections]
    assert "acp" in section_keys
    assert "llm" in section_keys  # kept for cost/pricing attribution

    sections = {s.key: s for s in schema.sections}
    acp_fields = {f.key: f for f in sections["acp"].fields}
    assert set(acp_fields) == {
        "acp_server",
        "acp_command",
        "acp_args",
        "acp_env",
        "acp_model",
        "acp_session_mode",
        "acp_prompt_timeout",
    }
    # Server picker + model are both critical — users pick server then
    # model. Raw command is a minor override for power users.
    assert acp_fields["acp_server"].prominence is SettingProminence.CRITICAL
    assert acp_fields["acp_model"].prominence is SettingProminence.CRITICAL
    assert acp_fields["acp_command"].prominence is SettingProminence.MINOR


def test_conversation_settings_export_schema_groups_sections() -> None:
    schema = ConversationSettings.export_schema()

    assert schema.model_name == "ConversationSettings"
    section_keys = [section.key for section in schema.sections]
    assert section_keys == ["general", "verification"]

    sections = {s.key: s for s in schema.sections}
    general_fields = {f.key: f for f in sections["general"].fields}
    assert set(general_fields) == {"max_iterations"}
    assert general_fields["max_iterations"].default == 500
    assert general_fields["max_iterations"].prominence is SettingProminence.MAJOR

    verification_fields = {f.key: f for f in sections["verification"].fields}
    assert set(verification_fields) == {
        "confirmation_mode",
        "security_analyzer",
    }
    assert verification_fields["confirmation_mode"].default is False
    assert (
        verification_fields["confirmation_mode"].prominence
        is SettingProminence.CRITICAL
    )
    assert verification_fields["security_analyzer"].default == "llm"
    assert verification_fields["security_analyzer"].choices[0].value == "llm"
    assert verification_fields["security_analyzer"].depends_on == ["confirmation_mode"]


def test_conversation_settings_model_dump_roundtrip() -> None:
    settings = ConversationSettings(
        max_iterations=42,
        confirmation_mode=True,
        security_analyzer="none",
    )

    restored = ConversationSettings.model_validate(settings.model_dump(mode="json"))

    assert restored == settings


def test_conversation_settings_create_request() -> None:
    settings = ConversationSettings(
        max_iterations=77,
        confirmation_mode=True,
        security_analyzer="llm",
    )
    workspace = LocalWorkspace(working_dir="/tmp")
    agent = OpenHandsAgentSettings(llm=LLM(model="test-model")).create_agent()

    request = settings.create_request(
        StartConversationRequest,
        agent=agent,
        workspace=workspace,
    )

    assert isinstance(request, StartConversationRequest)
    assert request.workspace == workspace
    assert request.max_iterations == 77
    assert isinstance(request.confirmation_policy, ConfirmRisky)
    assert isinstance(request.security_analyzer, LLMSecurityAnalyzer)

    overridden_request = settings.create_request(
        StartConversationRequest,
        agent=agent,
        workspace=workspace,
        max_iterations=5,
        confirmation_policy=AlwaysConfirm(),
        security_analyzer=None,
    )

    assert overridden_request.max_iterations == 5
    assert isinstance(overridden_request.confirmation_policy, AlwaysConfirm)
    assert overridden_request.security_analyzer is None


def test_conversation_settings_create_request_with_acp_agent() -> None:
    settings = ConversationSettings(
        max_iterations=77,
        confirmation_mode=True,
        security_analyzer="none",
    )
    workspace = LocalWorkspace(working_dir="/tmp")
    agent = ACPAgent(acp_command=["echo", "test"])

    request = settings.create_request(
        StartConversationRequest,
        agent=agent,
        workspace=workspace,
    )

    assert isinstance(request, StartConversationRequest)
    assert request.workspace == workspace
    assert request.max_iterations == 77
    assert isinstance(request.confirmation_policy, AlwaysConfirm)
    assert request.security_analyzer is None


# ---------------------------------------------------------------------------
# Schema export — combined (discriminated union)
# ---------------------------------------------------------------------------


def test_export_agent_settings_schema_emits_variant_tagged_sections() -> None:
    schema = export_agent_settings_schema()
    assert schema.model_name == "AgentSettings"

    by_keyvariant = {(s.key, s.variant): s for s in schema.sections}

    # Shared general section contains LLM-only top-level fields with
    # field-level variant="openhands" tags (so they hide on the ACP page).
    general = by_keyvariant.get(("general", None))
    assert general is not None
    general_keys = {f.key for f in general.fields}
    assert general_keys == {
        "agent",
        "tools",
        "enable_sub_agents",
        "enable_switch_llm_tool",
        "mcp_config",
    }
    # No agent_kind field — each variant has its own settings page and
    # injects the discriminator on save.
    assert "agent_kind" not in general_keys
    for f in general.fields:
        assert f.variant == "openhands", (
            f"expected field {f.key} variant=openhands, got {f.variant}"
        )

    # LLM-variant sections.
    assert ("llm", "openhands") in by_keyvariant
    assert ("condenser", "openhands") in by_keyvariant
    assert ("verification", "openhands") in by_keyvariant

    # ACP-variant sections.
    acp_section = by_keyvariant.get(("acp", "acp"))
    assert acp_section is not None
    acp_keys = {f.key for f in acp_section.fields}
    assert "acp_server" in acp_keys
    assert "acp_command" in acp_keys
    assert "acp_model" in acp_keys

    # acp_server is the critical user-visible field (the command is a
    # minor override).
    server_field = next(f for f in acp_section.fields if f.key == "acp_server")
    assert server_field.prominence is SettingProminence.CRITICAL
    server_choices = {c.value for c in server_field.choices}
    assert server_choices == {"claude-code", "codex", "gemini-cli", "custom"}

    command_field = next(f for f in acp_section.fields if f.key == "acp_command")
    assert command_field.prominence is SettingProminence.MINOR

    # ACP variant also has an LLM section (for cost/pricing attribution).
    assert ("llm", "acp") in by_keyvariant


# ---------------------------------------------------------------------------
# Discriminator + validation
# ---------------------------------------------------------------------------


def test_default_agent_settings_returns_openhands_variant() -> None:
    s = default_agent_settings()
    assert isinstance(s, OpenHandsAgentSettings)
    assert s.agent_kind == "openhands"


def test_validate_agent_settings_defaults_to_openhands_when_discriminator_missing() -> (
    None
):
    """Existing persisted payloads predate ``agent_kind`` — they must round-trip."""
    v = validate_agent_settings({"llm": {"model": "test-model"}})
    assert isinstance(v, OpenHandsAgentSettings)
    assert v.llm.model == "test-model"


def test_validate_agent_settings_dispatches_on_agent_kind() -> None:
    openhands = validate_agent_settings(
        {"agent_kind": "openhands", "llm": {"model": "m"}}
    )
    assert isinstance(openhands, OpenHandsAgentSettings)
    assert openhands.agent_kind == "openhands"

    legacy_llm = validate_agent_settings(
        {"agent_kind": "llm", "llm": {"model": "legacy-model"}}
    )
    assert isinstance(legacy_llm, OpenHandsAgentSettings)
    assert legacy_llm.agent_kind == "openhands"
    assert legacy_llm.llm.model == "legacy-model"

    acp = validate_agent_settings(
        {
            "agent_kind": "acp",
            "acp_command": ["npx", "-y", "claude-agent-acp"],
            "acp_model": "claude-opus-4-6",
        }
    )
    assert isinstance(acp, ACPAgentSettings)
    assert acp.acp_command == ["npx", "-y", "claude-agent-acp"]


def test_validate_agent_settings_migrates_v0_llm_payload() -> None:
    settings = validate_agent_settings({"llm": {"model": "test-model"}})

    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.schema_version == 3
    assert settings.agent_kind == "openhands"
    assert settings.llm.model == "test-model"


def test_validate_agent_settings_dispatches_current_acp_payload() -> None:
    settings = validate_agent_settings(
        {
            "schema_version": 1,
            "agent_kind": "acp",
            "acp_command": ["npx", "-y", "claude-agent-acp"],
            "acp_model": "claude-opus-4-6",
        }
    )

    assert isinstance(settings, ACPAgentSettings)
    # v1 → v2 → v3 keeps ACP payloads intact while bumping schema_version.
    assert settings.schema_version == 3
    assert settings.acp_command == ["npx", "-y", "claude-agent-acp"]


def test_validate_agent_settings_canonicalizes_legacy_llm_kind() -> None:
    """v1 payloads with the deprecated ``agent_kind: 'llm'`` are migrated to
    the canonical ``'openhands'`` discriminator on read."""
    settings = validate_agent_settings(
        {
            "schema_version": 1,
            "agent_kind": "llm",
            "llm": {"model": "legacy-model"},
        }
    )

    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.schema_version == 3
    assert settings.agent_kind == "openhands"
    assert settings.llm.model == "legacy-model"


def test_validate_agent_settings_drops_legacy_verification_fields() -> None:
    settings = validate_agent_settings(
        {
            "schema_version": 2,
            "agent_kind": "openhands",
            "verification": {
                "critic_enabled": True,
                "confirmation_mode": True,
                "security_analyzer": "llm",
            },
        }
    )

    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.schema_version == 3
    verification = settings.verification.model_dump(mode="json")
    assert verification["critic_enabled"] is True
    assert "confirmation_mode" not in verification
    assert "security_analyzer" not in verification


def test_validate_agent_settings_rejects_newer_schema_version() -> None:
    with pytest.raises(ValueError, match="newer than supported version 3"):
        validate_agent_settings({"schema_version": 4, "llm": {"model": "m"}})


def test_conversation_settings_from_persisted_migrates_v0_payload() -> None:
    settings = ConversationSettings.from_persisted({"max_iterations": 42})

    assert settings.schema_version == 1
    assert settings.max_iterations == 42


def test_conversation_settings_from_persisted_rejects_newer_schema_version() -> None:
    with pytest.raises(ValueError, match="newer than supported version 1"):
        ConversationSettings.from_persisted({"schema_version": 2})


# ---------------------------------------------------------------------------
# create_agent — LLM variant
# ---------------------------------------------------------------------------


def test_llm_create_agent_uses_settings_llm_and_tools() -> None:
    llm = LLM(model="test-model")
    tools = [Tool(name="TerminalTool")]
    settings = OpenHandsAgentSettings(llm=llm, tools=tools)
    agent = settings.create_agent()
    assert isinstance(agent, Agent)
    assert agent.llm is llm
    assert agent.tools == tools


def test_llm_agent_settings_validates_mcp_config_as_typed_model() -> None:
    settings = OpenHandsAgentSettings.model_validate(
        {
            "mcp_config": {
                "mcpServers": {
                    "fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}
                }
            }
        }
    )

    assert isinstance(settings.mcp_config, MCPConfig)
    assert settings.model_dump()["mcp_config"] == {
        "mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}
    }


def test_llm_create_agent_serializes_typed_mcp_config_compactly() -> None:
    mcp_config = MCPConfig.model_validate(
        {"mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}}
    )
    settings = OpenHandsAgentSettings(mcp_config=mcp_config)

    agent = settings.create_agent()

    assert agent.mcp_config == {
        "mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}
    }


def test_llm_create_agent_builds_condenser_when_enabled() -> None:
    llm = LLM(model="test-model", usage_id="agent")
    agent_metrics = llm.metrics
    settings = OpenHandsAgentSettings(
        llm=llm,
        condenser=CondenserSettings(enabled=True, max_size=100),
    )
    agent = settings.create_agent()

    assert agent.llm is llm
    assert isinstance(agent.condenser, LLMSummarizingCondenser)
    assert agent.condenser.max_size == 100
    assert agent.condenser.llm is not llm
    assert agent.condenser.llm.model == llm.model
    assert agent.condenser.llm.usage_id == "condenser"
    assert agent.condenser.llm.metrics is not agent_metrics


def test_llm_create_agent_no_condenser_when_disabled() -> None:
    settings = OpenHandsAgentSettings(
        condenser=CondenserSettings(enabled=False),
    )
    agent = settings.create_agent()
    assert agent.condenser is None


def test_llm_create_agent_builds_critic_when_enabled() -> None:
    settings = OpenHandsAgentSettings(
        llm=LLM(model="m", api_key=SecretStr("k")),
        verification=VerificationSettings(
            critic_enabled=True,
            critic_mode="all_actions",
        ),
    )
    agent = settings.create_agent()
    assert isinstance(agent.critic, APIBasedCritic)
    assert agent.critic.mode == "all_actions"
    assert agent.critic.iterative_refinement is None


def test_llm_create_agent_no_critic_without_api_key() -> None:
    settings = OpenHandsAgentSettings(
        llm=LLM(model="m", api_key=None),
        verification=VerificationSettings(critic_enabled=True),
    )
    agent = settings.create_agent()
    assert agent.critic is None


def test_llm_create_agent_critic_with_iterative_refinement() -> None:
    settings = OpenHandsAgentSettings(
        llm=LLM(model="m", api_key=SecretStr("k")),
        verification=VerificationSettings(
            critic_enabled=True,
            enable_iterative_refinement=True,
            critic_threshold=0.8,
            max_refinement_iterations=5,
        ),
    )
    agent = settings.create_agent()
    assert isinstance(agent.critic, APIBasedCritic)
    ir = agent.critic.iterative_refinement
    assert isinstance(ir, IterativeRefinementConfig)
    assert ir.success_threshold == 0.8
    assert ir.max_iterations == 5


def test_llm_roundtrip_preserves_llm_model() -> None:
    settings = OpenHandsAgentSettings(llm=LLM(model="test-model"))
    data = settings.model_dump()
    restored = OpenHandsAgentSettings.model_validate(data)
    assert restored.llm.model == "test-model"


# ---------------------------------------------------------------------------
# create_agent — ACP variant
# ---------------------------------------------------------------------------


def test_acp_create_agent_uses_server_default_command() -> None:
    """With ``acp_server`` set but no explicit command, use the built-in default."""
    settings = ACPAgentSettings(acp_server="claude-code", acp_model="claude-opus-4-6")
    agent = settings.create_agent()
    assert isinstance(agent, ACPAgent)
    assert agent.acp_command == [
        "npx",
        "-y",
        "@agentclientprotocol/claude-agent-acp",
    ]
    assert agent.acp_model == "claude-opus-4-6"


def test_acp_resolve_command_for_known_servers() -> None:
    """Every non-custom choice must map to a runnable default."""
    for server in ("claude-code", "codex", "gemini-cli"):
        settings = ACPAgentSettings(acp_server=server)
        cmd = settings.resolve_acp_command()
        assert cmd, f"expected default command for {server}, got empty"
        assert cmd[0] == "npx", f"expected npx-based default, got {cmd}"


def test_acp_create_agent_explicit_command_overrides_default() -> None:
    settings = ACPAgentSettings(
        acp_server="claude-code",
        acp_command=["my-local-acp-binary"],
    )
    agent = settings.create_agent()
    assert agent.acp_command == ["my-local-acp-binary"]


def test_acp_custom_server_requires_explicit_command() -> None:
    settings = ACPAgentSettings(acp_server="custom")
    try:
        settings.create_agent()
    except ValueError as e:
        assert "acp_command" in str(e) and "custom" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_acp_custom_server_with_command_resolves() -> None:
    settings = ACPAgentSettings(
        acp_server="custom",
        acp_command=["bin", "--flag"],
    )
    assert settings.resolve_acp_command() == ["bin", "--flag"]


def test_acp_api_key_env_var_maps_known_servers() -> None:
    assert (
        ACPAgentSettings(acp_server="claude-code").api_key_env_var
        == "ANTHROPIC_API_KEY"
    )
    assert ACPAgentSettings(acp_server="codex").api_key_env_var == "OPENAI_API_KEY"
    assert ACPAgentSettings(acp_server="gemini-cli").api_key_env_var == "GEMINI_API_KEY"
    assert (
        ACPAgentSettings(acp_server="custom", acp_command=["x"]).api_key_env_var is None
    )


def test_acp_resolve_provider_env_from_llm_credentials() -> None:
    settings = ACPAgentSettings(
        acp_server="gemini-cli",
        llm=LLM(
            model="gemini-2.5-pro",
            api_key=SecretStr("sk-test-gemini"),
            base_url="https://gemini-proxy.example.com",
        ),
    )

    assert settings.resolve_provider_env() == {
        "GEMINI_API_KEY": "sk-test-gemini",
        "GEMINI_BASE_URL": "https://gemini-proxy.example.com",
    }


def test_acp_resolve_provider_env_custom_server_empty() -> None:
    settings = ACPAgentSettings(
        acp_server="custom",
        acp_command=["custom-acp"],
        llm=LLM(
            model="custom-model",
            api_key=SecretStr("sk-test"),
            base_url="https://proxy.example.com",
        ),
    )

    assert settings.resolve_provider_env() == {}


def test_acp_resolve_acp_env_explicit_entries_override_provider_env() -> None:
    settings = ACPAgentSettings(
        acp_server="claude-code",
        llm=LLM(model="claude-opus-4-6", api_key=SecretStr("sk-ui-key")),
        acp_env={"ANTHROPIC_API_KEY": "sk-explicit-override"},
    )

    assert settings.resolve_acp_env() == {"ANTHROPIC_API_KEY": "sk-explicit-override"}


def test_acp_create_agent_passes_resolved_env_and_agent_context() -> None:
    context = AgentContext(secrets={"GITHUB_TOKEN": "ghp_test"})
    settings = ACPAgentSettings(
        acp_server="codex",
        llm=LLM(model="gpt-5.4", api_key=SecretStr("sk-openai")),
        agent_context=context,
    )

    agent = settings.create_agent()

    assert agent.acp_env == {"OPENAI_API_KEY": "sk-openai"}
    assert agent.agent_context == context


def test_llm_agent_settings_deprecated_alias_emits_warning() -> None:
    """Importing ``LLMAgentSettings`` emits DeprecationWarning at import time."""
    import openhands.sdk.settings as _settings_mod

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cls = getattr(_settings_mod, "LLMAgentSettings")

    assert any("LLMAgentSettings" in str(w.message) for w in caught), (
        f"expected deprecation warning, got: {[str(w.message) for w in caught]}"
    )
    assert issubclass(cls, OpenHandsAgentSettings)
    # Construction itself does not emit a second warning.
    settings = cls(llm=LLM(model="test-model"))
    assert isinstance(settings, OpenHandsAgentSettings)
    # LLMAgentSettings keeps its own agent_kind="llm" so the API-breakage
    # checker sees no field-value change vs the published PyPI release.
    assert settings.agent_kind == "llm"
    assert settings.llm.model == "test-model"


# ---------------------------------------------------------------------------
# ConversationSettings.create_request — dispatches on variant
# ---------------------------------------------------------------------------


def test_conversation_settings_create_request_for_llm_variant() -> None:
    settings = ConversationSettings(
        max_iterations=77,
        confirmation_mode=True,
        security_analyzer="llm",
    )
    workspace = LocalWorkspace(working_dir="/tmp")
    agent = OpenHandsAgentSettings(llm=LLM(model="test-model")).create_agent()

    request = settings.create_request(
        StartConversationRequest,
        agent=agent,
        workspace=workspace,
    )

    assert isinstance(request, StartConversationRequest)
    assert request.workspace == workspace
    assert request.max_iterations == 77
    assert isinstance(request.confirmation_policy, ConfirmRisky)
    assert isinstance(request.security_analyzer, LLMSecurityAnalyzer)


def test_conversation_settings_create_request_with_acp_agent_variant() -> None:
    settings = ConversationSettings(
        max_iterations=77,
        confirmation_mode=True,
        security_analyzer="none",
    )
    workspace = LocalWorkspace(working_dir="/tmp")
    agent = ACPAgentSettings(acp_command=["echo", "test"]).create_agent()

    request = settings.create_request(
        StartConversationRequest,
        agent=agent,
        workspace=workspace,
    )

    assert isinstance(request, StartConversationRequest)
    assert request.workspace == workspace
    assert request.max_iterations == 77
    assert isinstance(request.confirmation_policy, AlwaysConfirm)
    assert request.security_analyzer is None


def test_conversation_settings_agent_settings_field_accepts_both_variants() -> None:
    """The agent_settings runtime field should accept either variant."""
    llm_conv = ConversationSettings(
        agent_settings=OpenHandsAgentSettings(llm=LLM(model="m")),
    )
    assert isinstance(llm_conv.agent_settings, OpenHandsAgentSettings)

    acp_conv = ConversationSettings(
        agent_settings=ACPAgentSettings(acp_command=["x"]),
    )
    assert isinstance(acp_conv.agent_settings, ACPAgentSettings)


# ---------------------------------------------------------------------------
# Secret redaction in settings serialization
# ---------------------------------------------------------------------------


def test_acp_agent_settings_acp_env_redacted_by_default() -> None:
    settings = ACPAgentSettings(
        acp_command=["echo", "test"],
        acp_env={"OPENAI_API_KEY": "sk-real-secret"},
    )

    assert settings.acp_env["OPENAI_API_KEY"] == "sk-real-secret"
    assert "sk-real-secret" not in settings.model_dump_json()
    assert settings.model_dump(mode="json")["acp_env"] == {
        "OPENAI_API_KEY": "**********"
    }

    exposed = settings.model_dump(mode="json", context={"expose_secrets": True})
    assert exposed["acp_env"] == {"OPENAI_API_KEY": "sk-real-secret"}


def test_acp_agent_settings_acp_env_encrypts_with_cipher() -> None:
    """ACP env persistence should mirror other secret-bearing settings.

    The on-disk path encrypts values with a cipher, and loading with the same
    cipher must recover plaintext so ACP agents receive usable environment
    variables after settings are read back.
    """
    from openhands.sdk.utils.cipher import Cipher

    settings = ACPAgentSettings(
        acp_command=["echo", "test"],
        acp_env={"OPENAI_API_KEY": "sk-real-secret"},
    )
    cipher = Cipher(secret_key="test-encryption-key")

    dumped = settings.model_dump(mode="json", context={"cipher": cipher})
    encrypted_value = dumped["acp_env"]["OPENAI_API_KEY"]

    assert encrypted_value.startswith("gAAAA")
    assert "sk-real-secret" not in json.dumps(dumped)

    restored = ACPAgentSettings.model_validate(dumped, context={"cipher": cipher})
    assert restored.acp_env == {"OPENAI_API_KEY": "sk-real-secret"}

    restored_from_persisted = validate_agent_settings(
        dumped, context={"cipher": cipher}
    )
    assert isinstance(restored_from_persisted, ACPAgentSettings)
    assert restored_from_persisted.acp_env == {"OPENAI_API_KEY": "sk-real-secret"}

    legacy_plaintext = ACPAgentSettings.model_validate(
        {
            "acp_command": ["echo", "test"],
            "acp_env": {"OPENAI_API_KEY": "sk-legacy-plaintext"},
        },
        context={"cipher": cipher},
    )
    assert legacy_plaintext.acp_env == {"OPENAI_API_KEY": "sk-legacy-plaintext"}


def test_openhands_agent_settings_mcp_config_redacts_env_and_headers() -> None:
    mcp_config = MCPConfig.model_validate(
        {
            "mcpServers": {
                "leaky": {
                    "command": "echo",
                    "args": ["mcp"],
                    "env": {"API_KEY": "sk-mcp-secret"},
                    "headers": {"Authorization": "Bearer tok-mcp-secret"},
                }
            }
        }
    )
    settings = OpenHandsAgentSettings(mcp_config=mcp_config)

    blob = settings.model_dump_json()
    assert "sk-mcp-secret" not in blob
    assert "tok-mcp-secret" not in blob

    exposed = settings.model_dump(context={"expose_secrets": True})
    leaky = exposed["mcp_config"]["mcpServers"]["leaky"]
    assert leaky["env"]["API_KEY"] == "sk-mcp-secret"
    assert leaky["headers"]["Authorization"] == "Bearer tok-mcp-secret"


def test_mcp_config_encrypts_env_and_headers_with_cipher() -> None:
    """When a cipher is in the serialization context (the on-disk persistence
    path), MCP ``env`` / ``headers`` values must be encrypted per-value with
    that cipher — the same way other secret fields are persisted.

    Round-tripping through ``model_validate`` with the same cipher must
    recover the original plaintext values.
    """
    from openhands.sdk.utils.cipher import Cipher

    mcp_config = MCPConfig.model_validate(
        {
            "mcpServers": {
                "github": {
                    "command": "uvx",
                    "args": ["mcp-server-github"],
                    "env": {"GITHUB_TOKEN": "ghp-mcp-secret"},
                },
                "fetch": {
                    "url": "https://example.com/mcp",
                    "headers": {"Authorization": "Bearer tok-mcp-secret"},
                },
            }
        }
    )
    settings = OpenHandsAgentSettings(mcp_config=mcp_config)
    cipher = Cipher(secret_key="test-encryption-key")

    dumped = settings.model_dump(mode="json", context={"cipher": cipher})

    servers = dumped["mcp_config"]["mcpServers"]
    enc_token = servers["github"]["env"]["GITHUB_TOKEN"]
    enc_auth = servers["fetch"]["headers"]["Authorization"]

    # Plaintext values must NOT appear on disk.
    serialized = json.dumps(dumped)
    assert "ghp-mcp-secret" not in serialized
    assert "tok-mcp-secret" not in serialized
    assert "<redacted>" not in serialized

    # Values must be Fernet ciphertext (base64; starts with "gAAAA").
    assert enc_token.startswith("gAAAA")
    assert enc_auth.startswith("gAAAA")
    # Non-secret structure must remain plaintext.
    assert servers["github"]["command"] == "uvx"
    assert servers["github"]["args"] == ["mcp-server-github"]
    assert servers["fetch"]["url"] == "https://example.com/mcp"

    # Round-trip: decrypt with the same cipher recovers the originals.
    restored = OpenHandsAgentSettings.model_validate(dumped, context={"cipher": cipher})
    assert restored.mcp_config is not None
    restored_dump = restored.mcp_config.model_dump(exclude_none=True)
    assert (
        restored_dump["mcpServers"]["github"]["env"]["GITHUB_TOKEN"] == "ghp-mcp-secret"
    )
    assert (
        restored_dump["mcpServers"]["fetch"]["headers"]["Authorization"]
        == "Bearer tok-mcp-secret"
    )


def test_openhands_agent_settings_mcp_config_decrypt_legacy_plaintext_on_disk() -> None:
    """Loading a settings file that pre-dates per-value encryption (env /
    headers stored as plaintext) must NOT drop those values: each value that
    isn't a valid Fernet token is passed through unchanged so the next save
    can re-encrypt it.
    """
    from openhands.sdk.utils.cipher import Cipher

    cipher = Cipher(secret_key="test-encryption-key")
    legacy_payload = {
        "mcp_config": {
            "mcpServers": {
                "github": {
                    "command": "uvx",
                    "args": ["mcp-server-github"],
                    # plaintext, as the previous (pre-encryption) build wrote
                    "env": {"GITHUB_TOKEN": "ghp-legacy-plaintext"},
                }
            }
        }
    }

    restored = OpenHandsAgentSettings.model_validate(
        legacy_payload, context={"cipher": cipher}
    )
    assert restored.mcp_config is not None
    assert (
        restored.mcp_config.model_dump(exclude_none=True)["mcpServers"]["github"][
            "env"
        ]["GITHUB_TOKEN"]
        == "ghp-legacy-plaintext"
    )


def test_openhands_agent_settings_mcp_config_expose_encrypted_requires_cipher() -> None:
    """``expose_secrets="encrypted"`` without a cipher must raise — mirroring
    the contract used for individual ``SecretStr`` fields via
    :func:`serialize_secret`. Pydantic wraps the inner
    ``MissingCipherError`` in a ``PydanticSerializationError``; the
    agent-server's ``translate_missing_cipher`` walks the cause chain to
    surface a 503.
    """
    from pydantic_core import PydanticSerializationError

    from openhands.sdk.utils.pydantic_secrets import MissingCipherError

    settings = OpenHandsAgentSettings(
        mcp_config=MCPConfig.model_validate(
            {
                "mcpServers": {
                    "github": {
                        "command": "uvx",
                        "args": ["mcp-server-github"],
                        "env": {"GITHUB_TOKEN": "ghp-secret"},
                    }
                }
            }
        )
    )
    with pytest.raises(PydanticSerializationError) as exc_info:
        settings.model_dump(mode="json", context={"expose_secrets": "encrypted"})
    cause: BaseException | None = exc_info.value
    while cause is not None:
        if isinstance(cause, MissingCipherError):
            break
        cause = cause.__cause__ or cause.__context__
    assert isinstance(cause, MissingCipherError)


def test_openhands_agent_settings_mcp_config_expose_plaintext_passes_through() -> None:
    """``expose_secrets="plaintext"`` must return raw env / headers values
    even when a cipher is also in the context (e.g. an admin GET with
    explicit plaintext exposure).
    """
    from openhands.sdk.utils.cipher import Cipher

    settings = OpenHandsAgentSettings(
        mcp_config=MCPConfig.model_validate(
            {
                "mcpServers": {
                    "github": {
                        "command": "uvx",
                        "args": ["mcp-server-github"],
                        "env": {"GITHUB_TOKEN": "ghp-secret"},
                    }
                }
            }
        )
    )
    cipher = Cipher(secret_key="test-encryption-key")

    dumped = settings.model_dump(
        mode="json",
        context={"cipher": cipher, "expose_secrets": "plaintext"},
    )
    assert (
        dumped["mcp_config"]["mcpServers"]["github"]["env"]["GITHUB_TOKEN"]
        == "ghp-secret"
    )


def test_openhands_agent_settings_create_agent_keeps_real_mcp_secrets() -> None:
    # create_agent must hand the runtime real env/headers (the field serializer
    # redacts mcp_config for transit only).
    mcp_config = MCPConfig.model_validate(
        {
            "mcpServers": {
                "leaky": {
                    "command": "echo",
                    "args": ["mcp"],
                    "env": {"API_KEY": "sk-mcp-secret"},
                }
            }
        }
    )
    agent = OpenHandsAgentSettings(mcp_config=mcp_config).create_agent()

    assert agent.mcp_config["mcpServers"]["leaky"]["env"]["API_KEY"] == "sk-mcp-secret"


# ---------------------------------------------------------------------------
# AgentSettingsBase — shared interface
# ---------------------------------------------------------------------------


def test_agent_settings_base_is_parent_of_both_variants() -> None:
    assert issubclass(OpenHandsAgentSettings, AgentSettingsBase)
    assert issubclass(ACPAgentSettings, AgentSettingsBase)


def test_agent_settings_base_schema_version_inherited() -> None:
    openhands = OpenHandsAgentSettings()
    acp = ACPAgentSettings(acp_command=["x"])
    assert openhands.schema_version == AGENT_SETTINGS_SCHEMA_VERSION
    assert acp.schema_version == AGENT_SETTINGS_SCHEMA_VERSION


def test_agent_settings_base_export_schema_works_on_both_variants() -> None:
    openhands_schema = OpenHandsAgentSettings.export_schema()
    acp_schema = ACPAgentSettings.export_schema()
    assert openhands_schema.model_name == "OpenHandsAgentSettings"
    assert acp_schema.model_name == "ACPAgentSettings"


def test_agent_settings_base_create_agent_is_callable_via_interface() -> None:
    """Both variants expose create_agent() through the shared base type."""
    settings: AgentSettingsBase = OpenHandsAgentSettings(llm=LLM(model="test-model"))
    agent = settings.create_agent()
    assert isinstance(agent, Agent)

    acp_settings: AgentSettingsBase = ACPAgentSettings(acp_command=["x"])
    from openhands.sdk.agent.acp_agent import ACPAgent

    acp_agent = acp_settings.create_agent()
    assert isinstance(acp_agent, ACPAgent)


# ---------------------------------------------------------------------------
# ACPAgentSettings — provider registry integration
# ---------------------------------------------------------------------------


def test_acp_settings_provider_info_returns_registry_entry() -> None:
    settings = ACPAgentSettings(acp_server="claude-code")
    info = settings.provider_info
    assert info is not None
    assert info.key == "claude-code"
    assert info.display_name == "Claude Code"


def test_acp_settings_provider_info_returns_none_for_custom() -> None:
    settings = ACPAgentSettings(acp_server="custom", acp_command=["x"])
    assert settings.provider_info is None


def test_acp_settings_api_key_env_var_from_registry() -> None:
    assert (
        ACPAgentSettings(acp_server="claude-code").api_key_env_var
        == "ANTHROPIC_API_KEY"
    )
    assert ACPAgentSettings(acp_server="codex").api_key_env_var == "OPENAI_API_KEY"
    assert ACPAgentSettings(acp_server="gemini-cli").api_key_env_var == "GEMINI_API_KEY"
    assert (
        ACPAgentSettings(acp_server="custom", acp_command=["x"]).api_key_env_var is None
    )


def test_acp_settings_base_url_env_var_from_registry() -> None:
    assert (
        ACPAgentSettings(acp_server="claude-code").base_url_env_var
        == "ANTHROPIC_BASE_URL"
    )
    assert ACPAgentSettings(acp_server="codex").base_url_env_var == "OPENAI_BASE_URL"
    assert (
        ACPAgentSettings(acp_server="gemini-cli").base_url_env_var == "GEMINI_BASE_URL"
    )
    assert (
        ACPAgentSettings(acp_server="custom", acp_command=["x"]).base_url_env_var
        is None
    )


def test_acp_resolve_command_uses_registry_defaults() -> None:
    from openhands.sdk.settings.acp_providers import ACP_PROVIDERS

    for server_key in ("claude-code", "codex", "gemini-cli"):
        settings = ACPAgentSettings(acp_server=server_key)
        expected = list(ACP_PROVIDERS[server_key].default_command)
        assert settings.resolve_acp_command() == expected


# ---------------------------------------------------------------------------
# Agent capability helpers
# ---------------------------------------------------------------------------


def test_regular_agent_supports_all_capabilities() -> None:
    agent = OpenHandsAgentSettings(llm=LLM(model="test-model")).create_agent()
    assert agent.supports_openhands_tools is True
    assert agent.supports_openhands_mcp is True
    assert agent.supports_condenser is True
    assert agent.agent_kind == "openhands"


def test_acp_agent_reports_no_openhands_capabilities() -> None:
    from openhands.sdk.agent.acp_agent import ACPAgent

    agent = ACPAgent(acp_command=["x"])
    assert agent.supports_openhands_tools is False
    assert agent.supports_openhands_mcp is False
    assert agent.supports_condenser is False
    assert agent.agent_kind == "acp"
