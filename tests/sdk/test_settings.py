from fastmcp.mcp_config import MCPConfig
from pydantic import SecretStr

from openhands.agent_server.models import (
    StartACPConversationRequest,
    StartConversationRequest,
)
from openhands.sdk import (
    LLM,
    Agent,
    AgentSettings,
    ConversationSettings,
    SettingProminence,
    Tool,
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
# Schema export
# ---------------------------------------------------------------------------


def test_agent_settings_export_schema_groups_sections() -> None:
    schema = AgentSettings.export_schema()

    assert schema.model_name == "AgentSettings"
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
    assert set(general_fields) == {"agent", "tools", "mcp_config"}
    assert general_fields["agent"].default == "CodeActAgent"
    assert general_fields["agent"].prominence is SettingProminence.MAJOR
    assert general_fields["tools"].value_type == "array"
    assert general_fields["tools"].default == []
    assert general_fields["tools"].prominence is SettingProminence.MAJOR

    # -- llm section --
    llm_fields = {f.key: f for f in sections["llm"].fields}
    expected_llm_keys = {
        f"llm.{name}" for name in LLM.model_fields if name not in _LLM_EXCLUDED_FIELDS
    }
    assert set(llm_fields) == expected_llm_keys

    assert llm_fields["llm.model"].value_type == "string"
    assert llm_fields["llm.model"].prominence is SettingProminence.CRITICAL
    assert llm_fields["llm.api_key"].label == "API Key"
    assert llm_fields["llm.api_key"].value_type == "string"
    assert llm_fields["llm.api_key"].secret is True
    assert llm_fields["llm.api_key"].prominence is SettingProminence.CRITICAL
    assert llm_fields["llm.base_url"].prominence is SettingProminence.CRITICAL
    assert llm_fields["llm.reasoning_effort"].choices[0].value == "low"
    assert llm_fields["llm.reasoning_effort"].prominence is SettingProminence.MINOR
    assert llm_fields["llm.litellm_extra_body"].value_type == "object"
    assert llm_fields["llm.litellm_extra_body"].default == {}
    assert llm_fields["llm.litellm_extra_body"].prominence is SettingProminence.MINOR
    llm_model_field_extra = LLM.model_fields["model"].json_schema_extra
    assert isinstance(llm_model_field_extra, dict)
    assert "openhands_settings" in llm_model_field_extra
    schema_dump = schema.model_dump(mode="json")
    assert "required" not in schema_dump["sections"][0]["fields"][0]

    assert llm_fields["llm.num_retries"].prominence is SettingProminence.MINOR

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
    assert v_fields["verification.critic_mode"].depends_on == [
        "verification.critic_enabled"
    ]
    assert v_fields["verification.critic_mode"].prominence is SettingProminence.MINOR
    assert v_fields["verification.critic_threshold"].depends_on == [
        "verification.critic_enabled",
        "verification.enable_iterative_refinement",
    ]
    assert (
        v_fields["verification.critic_threshold"].prominence is SettingProminence.MINOR
    )
    assert "confirmation_mode" not in v_fields
    assert "security_analyzer" not in v_fields


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

    assert len(sections["verification"].fields) == 2
    verification_fields = {f.key: f for f in sections["verification"].fields}
    assert set(verification_fields) == {
        "confirmation_mode",
        "security_analyzer",
    }
    assert verification_fields["confirmation_mode"].default is False
    assert (
        verification_fields["confirmation_mode"].prominence is SettingProminence.MAJOR
    )
    assert verification_fields["security_analyzer"].default == "llm"
    assert verification_fields["security_analyzer"].choices[0].value == "llm"
    assert verification_fields["security_analyzer"].depends_on == ["confirmation_mode"]


def test_verification_settings_legacy_controls_are_excluded_from_dump() -> None:
    settings = VerificationSettings(
        confirmation_mode=True,
        security_analyzer="llm",
    )

    assert settings.confirmation_mode is True
    assert settings.security_analyzer == "llm"

    dumped = settings.model_dump(mode="json")
    assert "confirmation_mode" not in dumped
    assert "security_analyzer" not in dumped


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
    agent = AgentSettings(llm=LLM(model="test-model")).create_agent()

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
# create_agent
# ---------------------------------------------------------------------------


def test_create_agent_uses_settings_llm_and_tools() -> None:
    llm = LLM(model="test-model")
    tools = [Tool(name="TerminalTool")]
    settings = AgentSettings(llm=llm, tools=tools)
    agent = settings.create_agent()
    assert isinstance(agent, Agent)
    assert agent.llm is llm
    assert agent.tools == tools


def test_agent_settings_validates_mcp_config_as_typed_model() -> None:
    settings = AgentSettings.model_validate(
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


def test_create_agent_serializes_typed_mcp_config_compactly() -> None:
    mcp_config = MCPConfig.model_validate(
        {"mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}}
    )
    settings = AgentSettings(mcp_config=mcp_config)

    agent = settings.create_agent()

    assert agent.mcp_config == {
        "mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}
    }


def test_create_agent_builds_condenser_when_enabled() -> None:
    settings = AgentSettings(
        condenser=CondenserSettings(enabled=True, max_size=100),
    )
    agent = settings.create_agent()
    assert isinstance(agent.condenser, LLMSummarizingCondenser)
    assert agent.condenser.max_size == 100
    assert agent.condenser.llm is agent.llm


def test_create_agent_no_condenser_when_disabled() -> None:
    settings = AgentSettings(
        condenser=CondenserSettings(enabled=False),
    )
    agent = settings.create_agent()
    assert agent.condenser is None


def test_create_agent_builds_critic_when_enabled() -> None:
    settings = AgentSettings(
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


def test_create_agent_no_critic_when_disabled() -> None:
    settings = AgentSettings(
        verification=VerificationSettings(critic_enabled=False),
    )
    agent = settings.create_agent()
    assert agent.critic is None


def test_create_agent_no_critic_without_api_key() -> None:
    """Critic requires auth — skip if the LLM has no api_key."""
    settings = AgentSettings(
        llm=LLM(model="m", api_key=None),
        verification=VerificationSettings(critic_enabled=True),
    )
    agent = settings.create_agent()
    assert agent.critic is None


def test_create_agent_critic_with_iterative_refinement() -> None:
    settings = AgentSettings(
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


def test_create_agent_critic_with_server_url_override() -> None:
    settings = AgentSettings(
        llm=LLM(model="m", api_key=SecretStr("k")),
        verification=VerificationSettings(
            critic_enabled=True,
            critic_server_url="https://proxy.example.com/vllm",
            critic_model_name="my-critic",
        ),
    )
    agent = settings.create_agent()
    assert isinstance(agent.critic, APIBasedCritic)
    assert agent.critic.server_url == "https://proxy.example.com/vllm"
    assert agent.critic.model_name == "my-critic"


def test_roundtrip_preserves_llm_model() -> None:
    settings = AgentSettings(llm=LLM(model="test-model"))
    data = settings.model_dump()
    restored = AgentSettings.model_validate(data)
    assert restored.llm.model == "test-model"


def test_agent_settings_model_validate_accepts_nested_payload() -> None:
    settings = AgentSettings.model_validate(
        {
            "schema_version": AgentSettings.CURRENT_PERSISTED_VERSION,
            "llm": {
                "model": "test-model",
                "api_key": "secret-key",
            },
            "verification": {"critic_enabled": True},
        }
    )

    assert settings.schema_version == AgentSettings.CURRENT_PERSISTED_VERSION
    assert settings.llm.model == "test-model"
    assert isinstance(settings.llm.api_key, SecretStr)
    assert settings.llm.api_key.get_secret_value() == "secret-key"
    assert settings.verification.critic_enabled is True


def test_agent_settings_model_dump_roundtrip_preserves_sparse_updates() -> None:
    settings = AgentSettings.model_validate(
        {
            "schema_version": AgentSettings.CURRENT_PERSISTED_VERSION,
            "llm": {"model": "base-model", "base_url": "https://example.com"},
        }
    )

    updated = settings.model_copy(
        update={
            "llm": settings.llm.model_copy(
                update={"model": "personal-model", "base_url": None}
            )
        }
    )
    restored = AgentSettings.model_validate(
        updated.model_dump(mode="json", context={"expose_secrets": True})
    )

    assert restored.llm.model == "personal-model"
    assert restored.llm.base_url is None
    assert restored.schema_version == AgentSettings.CURRENT_PERSISTED_VERSION
