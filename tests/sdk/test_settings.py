from pydantic import SecretStr

from openhands.sdk import LLM, Agent, AgentSettings, SettingProminence, Tool
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.critic.base import IterativeRefinementConfig
from openhands.sdk.critic.impl.api import APIBasedCritic
from openhands.sdk.settings import CondenserSettings, VerificationSettings


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
    assert "agent" in general_fields
    assert general_fields["agent"].default == "CodeActAgent"
    assert general_fields["agent"].prominence is SettingProminence.MAJOR

    # -- llm section --
    llm_fields = {f.key: f for f in sections["llm"].fields}
    expected_llm_keys = {
        f"llm.{name}" for name in LLM.model_fields if name not in _LLM_EXCLUDED_FIELDS
    }
    assert set(llm_fields) == expected_llm_keys

    assert llm_fields["llm.model"].required is True
    assert llm_fields["llm.model"].value_type == "string"
    assert llm_fields["llm.model"].prominence is SettingProminence.CRITICAL
    assert llm_fields["llm.api_key"].label == "API Key"
    assert llm_fields["llm.api_key"].value_type == "string"
    assert llm_fields["llm.api_key"].required is False
    assert llm_fields["llm.api_key"].secret is True
    assert llm_fields["llm.api_key"].prominence is SettingProminence.CRITICAL
    assert llm_fields["llm.base_url"].prominence is SettingProminence.CRITICAL
    assert llm_fields["llm.reasoning_effort"].choices[0].value == "low"
    assert llm_fields["llm.reasoning_effort"].prominence is SettingProminence.MAJOR
    assert llm_fields["llm.litellm_extra_body"].value_type == "object"
    assert llm_fields["llm.litellm_extra_body"].default == {}
    assert llm_fields["llm.litellm_extra_body"].prominence is SettingProminence.MINOR
    llm_model_field_extra = LLM.model_fields["model"].json_schema_extra
    assert not llm_model_field_extra

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

    # -- verification section (critic + security combined) --
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
    assert v_fields["verification.confirmation_mode"].value_type == "boolean"
    assert v_fields["verification.confirmation_mode"].default is False
    assert (
        v_fields["verification.confirmation_mode"].prominence is SettingProminence.MAJOR
    )
    assert v_fields["verification.security_analyzer"].choices[0].value == "llm"
    assert v_fields["verification.security_analyzer"].depends_on == [
        "verification.confirmation_mode"
    ]


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
