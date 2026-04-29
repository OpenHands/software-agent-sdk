import warnings

import pytest
from fastmcp.mcp_config import MCPConfig
from pydantic import SecretStr

from openhands.agent_server.models import (
    StartACPConversationRequest,
    StartConversationRequest,
)
from openhands.sdk import (
    LLM,
    ACPAgentSettings,
    Agent,
    AgentSettings,
    ConversationSettings,
    LLMAgentSettings,
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
from openhands.sdk.settings import CondenserSettings, VerificationSettings
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

    # -- llm section --
    llm_fields = {f.key: f for f in sections["llm"].fields}
    expected_llm_keys = {
        f"llm.{name}" for name in LLM.model_fields if name not in _LLM_EXCLUDED_FIELDS
    }
    assert set(llm_fields) == expected_llm_keys

    assert llm_fields["llm.model"].value_type == "string"
    assert llm_fields["llm.model"].prominence is SettingProminence.CRITICAL
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


def test_conversation_settings_create_request_for_acp() -> None:
    settings = ConversationSettings(
        max_iterations=77,
        confirmation_mode=True,
        security_analyzer="none",
    )
    workspace = LocalWorkspace(working_dir="/tmp")
    agent = ACPAgent(acp_command=["echo", "test"])

    request = settings.create_request(
        StartACPConversationRequest,
        agent=agent,
        workspace=workspace,
    )

    assert isinstance(request, StartACPConversationRequest)
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
    assert general_keys == {"agent", "tools", "enable_sub_agents", "mcp_config"}
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

    acp = validate_agent_settings(
        {
            "agent_kind": "acp",
            "acp_command": ["npx", "-y", "claude-agent-acp"],
            "acp_model": "claude-opus-4-6",
        }
    )
    assert isinstance(acp, ACPAgentSettings)
    assert acp.acp_command == ["npx", "-y", "claude-agent-acp"]


def test_agent_settings_from_persisted_migrates_v0_llm_payload() -> None:
    settings = AgentSettings.from_persisted({"llm": {"model": "test-model"}})

    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.schema_version == 1
    assert settings.agent_kind == "openhands"
    assert settings.llm.model == "test-model"


def test_agent_settings_from_persisted_dispatches_current_acp_payload() -> None:
    settings = AgentSettings.from_persisted(
        {
            "schema_version": 1,
            "agent_kind": "acp",
            "acp_command": ["npx", "-y", "claude-agent-acp"],
            "acp_model": "claude-opus-4-6",
        }
    )

    assert isinstance(settings, ACPAgentSettings)
    assert settings.schema_version == 1
    assert settings.acp_command == ["npx", "-y", "claude-agent-acp"]


def test_agent_settings_from_persisted_rejects_newer_schema_version() -> None:
    with pytest.raises(ValueError, match="newer than supported version 1"):
        AgentSettings.from_persisted({"schema_version": 2, "llm": {"model": "m"}})


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
    settings = OpenHandsAgentSettings(
        condenser=CondenserSettings(enabled=True, max_size=100),
    )
    agent = settings.create_agent()
    assert isinstance(agent.condenser, LLMSummarizingCondenser)
    assert agent.condenser.max_size == 100


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


# ---------------------------------------------------------------------------
# Legacy ``AgentSettings`` compatibility
# ---------------------------------------------------------------------------


def test_legacy_agent_settings_still_instantiates_as_llm_variant() -> None:
    """``AgentSettings(...)`` is retained (deprecated) as a OpenHandsAgentSettings subclass.

    All v1.17.0 attributes must remain reachable so the API breakage
    check does not flag them as removed.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        settings = AgentSettings(llm=LLM(model="test-model"))

    # The legacy name emits a DeprecationWarning on construction. The
    # warning's scheduled removal is in 1.22.0 per the class docstring.
    assert any("AgentSettings" in str(w.message) for w in caught), (
        f"expected deprecation warning, got: {[str(w.message) for w in caught]}"
    )

    # It remains a OpenHandsAgentSettings subclass so existing code paths work.
    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.agent_kind == "openhands"
    assert settings.llm.model == "test-model"


def test_legacy_agent_settings_retains_all_v1_17_attributes() -> None:
    """Guardrail mirroring the API breakage CI check: don't silently remove fields."""
    fields = AgentSettings.model_fields
    assert {
        "schema_version",
        "agent",
        "llm",
        "tools",
        "mcp_config",
        "agent_context",
        "condenser",
        "verification",
    }.issubset(set(fields))

    # Methods defined on the original class must still resolve via
    # inheritance.
    for name in ("export_schema", "create_agent", "build_condenser", "build_critic"):
        assert hasattr(AgentSettings, name), f"missing: AgentSettings.{name}"


def test_llm_agent_settings_deprecated_alias_emits_warning() -> None:
    """``LLMAgentSettings(...)`` emits a DeprecationWarning; is OpenHandsAgentSettings subclass."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        settings = LLMAgentSettings(llm=LLM(model="test-model"))

    assert any("LLMAgentSettings" in str(w.message) for w in caught), (
        f"expected deprecation warning, got: {[str(w.message) for w in caught]}"
    )
    assert isinstance(settings, OpenHandsAgentSettings)
    assert settings.agent_kind == "openhands"
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


def test_conversation_settings_create_request_for_acp_variant() -> None:
    settings = ConversationSettings(
        max_iterations=77,
        confirmation_mode=True,
        security_analyzer="none",
    )
    workspace = LocalWorkspace(working_dir="/tmp")
    agent = ACPAgentSettings(acp_command=["echo", "test"]).create_agent()

    request = settings.create_request(
        StartACPConversationRequest,
        agent=agent,
        workspace=workspace,
    )

    assert isinstance(request, StartACPConversationRequest)
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
