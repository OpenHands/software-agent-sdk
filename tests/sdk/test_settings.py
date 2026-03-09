from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Agent,
    AgentSettings,
    CondenserSettings,
    CriticSettings,
    LLMSettings,
    LLMSummarizingCondenser,
)
from openhands.sdk.critic import IterativeRefinementConfig
from openhands.sdk.critic.impl.api import APIBasedCritic


def test_agent_settings_from_agent_and_apply_to_agent(monkeypatch) -> None:
    monkeypatch.setenv("ALLOW_SHORT_CONTEXT_WINDOWS", "true")

    agent = Agent(
        llm=LLM(
            model="openai/gpt-4o",
            api_key=SecretStr("llm-key"),
            base_url="https://llm.example",
            timeout=180,
            max_input_tokens=4096,
        ),
        condenser=LLMSummarizingCondenser(
            llm=LLM(
                model="openai/gpt-4o",
                api_key=SecretStr("llm-key"),
                usage_id="condenser",
            ),
            max_size=320,
        ),
        critic=APIBasedCritic(
            api_key=SecretStr("critic-key"),
            mode="all_actions",
            iterative_refinement=IterativeRefinementConfig(
                success_threshold=0.7,
                max_iterations=5,
            ),
        ),
    )

    settings = AgentSettings.from_agent(agent)

    assert settings.llm.model == "openai/gpt-4o"
    assert settings.llm.base_url == "https://llm.example"
    assert settings.llm.timeout == 180
    assert settings.llm.max_input_tokens == 4096
    assert settings.condenser.enabled is True
    assert settings.condenser.max_size == 320
    assert settings.critic.enabled is True
    assert settings.critic.mode == "all_actions"
    assert settings.critic.enable_iterative_refinement is True
    assert settings.critic.threshold == 0.7
    assert settings.critic.max_refinement_iterations == 5

    updated_settings = settings.model_copy(
        update={
            "condenser": settings.condenser.model_copy(update={"max_size": 256}),
            "critic": settings.critic.model_copy(
                update={"threshold": 0.8, "max_refinement_iterations": 2}
            ),
            "llm": settings.llm.model_copy(update={"timeout": 90}),
        }
    )
    updated_agent = updated_settings.apply_to_agent(agent)

    assert updated_agent.llm.timeout == 90
    assert isinstance(updated_agent.condenser, LLMSummarizingCondenser)
    assert updated_agent.condenser.max_size == 256
    assert isinstance(updated_agent.critic, APIBasedCritic)
    assert updated_agent.critic.mode == "all_actions"
    assert updated_agent.critic.iterative_refinement is not None
    assert updated_agent.critic.iterative_refinement.success_threshold == 0.8
    assert updated_agent.critic.iterative_refinement.max_iterations == 2


def test_agent_settings_to_agent_uses_factories() -> None:
    settings = AgentSettings(
        llm=LLMSettings(
            model="openai/gpt-4o",
            api_key=SecretStr("llm-key"),
            base_url="https://llm.example",
        ),
        condenser=CondenserSettings(enabled=True, max_size=300),
        critic=CriticSettings(
            enabled=True,
            enable_iterative_refinement=True,
            threshold=0.65,
            max_refinement_iterations=4,
        ),
    )

    def build_agent(llm: LLM) -> Agent:
        return Agent(llm=llm)

    def build_critic(
        llm: LLM,
        agent_settings: AgentSettings,
        agent: Agent | None,
    ) -> APIBasedCritic:
        assert agent is not None
        assert agent_settings.critic.enabled is True
        return APIBasedCritic(
            server_url=f"{llm.base_url}/critic",
            api_key=llm.api_key or SecretStr("fallback-key"),
            model_name="critic",
        )

    agent = settings.to_agent(agent_factory=build_agent, critic_factory=build_critic)

    assert agent.llm.model == "openai/gpt-4o"
    assert agent.llm.base_url == "https://llm.example"
    assert isinstance(agent.condenser, LLMSummarizingCondenser)
    assert agent.condenser.max_size == 300
    assert agent.condenser.llm.usage_id == "condenser"
    assert isinstance(agent.critic, APIBasedCritic)
    assert agent.critic.server_url == "https://llm.example/critic"
    assert agent.critic.iterative_refinement is not None
    assert agent.critic.iterative_refinement.success_threshold == 0.65
    assert agent.critic.iterative_refinement.max_iterations == 4


def test_agent_settings_export_schema_groups_sections() -> None:
    schema = AgentSettings.export_schema()

    assert schema.model_name == "AgentSettings"
    assert [section.key for section in schema.sections] == [
        "llm",
        "condenser",
        "critic",
    ]

    llm_fields = {field.key: field for field in schema.sections[0].fields}
    assert llm_fields["llm.model"].required is True
    assert llm_fields["llm.api_key"].widget == "password"
    assert llm_fields["llm.api_key"].required is False
    assert llm_fields["llm.api_key"].secret is True

    critic_fields = {field.key: field for field in schema.sections[2].fields}
    assert critic_fields["critic.mode"].widget == "select"
    assert [choice.value for choice in critic_fields["critic.mode"].choices] == [
        "finish_and_message",
        "all_actions",
    ]
    assert critic_fields["critic.threshold"].depends_on == [
        "critic.enabled",
        "critic.enable_iterative_refinement",
    ]
