from pydantic import SecretStr

from openhands.sdk import LLM, Agent, LLMSummarizingCondenser
from openhands.sdk.critic import IterativeRefinementConfig
from openhands.sdk.critic.impl.api import APIBasedCritic
from openhands.sdk.settings import SDKSettings


def test_sdk_settings_from_agent_and_apply_to_agent(monkeypatch) -> None:
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

    settings = SDKSettings.from_agent(agent)

    assert settings.llm_model == "openai/gpt-4o"
    assert settings.llm_base_url == "https://llm.example"
    assert settings.llm_timeout == 180
    assert settings.llm_max_input_tokens == 4096
    assert settings.enable_default_condenser is True
    assert settings.condenser_max_size == 320
    assert settings.enable_critic is True
    assert settings.critic_mode == "all_actions"
    assert settings.enable_iterative_refinement is True
    assert settings.critic_threshold == 0.7
    assert settings.max_refinement_iterations == 5

    updated_settings = settings.model_copy(
        update={
            "condenser_max_size": 256,
            "critic_threshold": 0.8,
            "max_refinement_iterations": 2,
            "llm_timeout": 90,
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


def test_sdk_settings_to_agent_uses_factories() -> None:
    settings = SDKSettings(
        llm_model="openai/gpt-4o",
        llm_api_key=SecretStr("llm-key"),
        llm_base_url="https://llm.example",
        enable_default_condenser=True,
        condenser_max_size=300,
        enable_critic=True,
        enable_iterative_refinement=True,
        critic_threshold=0.65,
        max_refinement_iterations=4,
    )

    def build_agent(llm: LLM) -> Agent:
        return Agent(llm=llm)

    def build_critic(
        llm: LLM,
        sdk_settings: SDKSettings,
        agent: Agent | None,
    ) -> APIBasedCritic:
        assert agent is not None
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


def test_sdk_settings_export_schema_groups_sections() -> None:
    schema = SDKSettings.export_schema()

    assert [section.key for section in schema.sections] == [
        "llm",
        "condenser",
        "critic",
    ]

    llm_fields = {field.key: field for field in schema.sections[0].fields}
    assert llm_fields["llm_api_key"].widget == "password"
    assert llm_fields["llm_api_key"].secret is True

    critic_fields = {field.key: field for field in schema.sections[2].fields}
    assert critic_fields["critic_mode"].widget == "select"
    assert [choice.value for choice in critic_fields["critic_mode"].choices] == [
        "finish_and_message",
        "all_actions",
    ]
    assert critic_fields["critic_threshold"].depends_on == [
        "enable_critic",
        "enable_iterative_refinement",
    ]
