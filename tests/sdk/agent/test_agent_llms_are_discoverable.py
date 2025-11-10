from pydantic import Field

from openhands.sdk import LLM, Agent, LLMSummarizingCondenser
from openhands.sdk.llm.router import MultimodalRouter


def check_usage_id_exists(usage_id: str, llms: list[LLM]):
    usage_ids = [llm.usage_id for llm in llms]
    return usage_id in usage_ids


def test_automatic_llm_discovery():
    llm_service_id = "main-agent"
    agent = Agent(llm=LLM(model="test-model", usage_id=llm_service_id))

    llms = list(agent.get_all_llms())
    assert len(llms) == 1
    assert check_usage_id_exists(llm_service_id, llms)


def test_automatic_llm_discovery_for_multiple_llms():
    llm_service_id = "main-agent"
    condenser_service_id = "condenser"

    condenser = LLMSummarizingCondenser(
        llm=LLM(model="test-model", usage_id=condenser_service_id)
    )

    agent = Agent(
        llm=LLM(model="test-model", usage_id=llm_service_id), condenser=condenser
    )

    llms = list(agent.get_all_llms())
    assert len(llms) == 2
    assert check_usage_id_exists(llm_service_id, llms)
    assert check_usage_id_exists(condenser_service_id, llms)


def test_automatic_llm_discovery_for_custom_agent_with_duplicates():
    class CustomAgent(Agent):
        model_routers: list[LLM] = Field(default_factory=list)

    llm_service_id = "main-agent"
    router_service_id = "secondary_llm"
    router_service_id_2 = "tertiary_llm"
    condenser_service_id = "condenser"

    condenser = LLMSummarizingCondenser(
        llm=LLM(model="test-model", usage_id=condenser_service_id)
    )

    agent_llm = LLM(model="test-model", usage_id=llm_service_id)
    router_llm = LLM(model="test-model", usage_id=router_service_id)
    router_llm_2 = LLM(model="test-model", usage_id=router_service_id_2)

    agent = CustomAgent(
        llm=agent_llm,
        condenser=condenser,
        model_routers=[agent_llm, router_llm, router_llm_2],
    )

    llms = list(agent.get_all_llms())
    assert len(llms) == 4
    assert check_usage_id_exists(llm_service_id, llms)
    assert check_usage_id_exists(router_service_id, llms)
    assert check_usage_id_exists(router_service_id_2, llms)
    assert check_usage_id_exists(condenser_service_id, llms)


def test_automatic_llm_discovery_with_multimodal_router():
    """Test that LLMs inside a MultimodalRouter are discovered correctly."""
    primary_service_id = "primary-llm"
    secondary_service_id = "secondary-llm"

    # Create LLMs for the router
    primary_llm = LLM(model="test-primary-model", usage_id=primary_service_id)
    secondary_llm = LLM(model="test-secondary-model", usage_id=secondary_service_id)

    # Create MultimodalRouter with the LLMs
    multimodal_router = MultimodalRouter(
        usage_id="multimodal-router",
        llms_for_routing={"primary": primary_llm, "secondary": secondary_llm},
    )

    # Create agent with the router
    agent = Agent(llm=multimodal_router)

    # Get all LLMs and verify they are discovered
    llms = list(agent.get_all_llms())

    # Only the raw LLMs inside the router should be found (not the router itself)
    assert len(llms) == 2
    assert check_usage_id_exists(primary_service_id, llms)
    assert check_usage_id_exists(secondary_service_id, llms)


def test_automatic_llm_discovery_with_llm_as_base_class():
    class NewLLM(LLM):
        list_llms: list[LLM] = Field(default_factory=list)
        dict_llms: dict[str, LLM] = Field(default_factory=dict)
        raw_llm: LLM | None = None

    list_llm = LLM(model="list-model", usage_id="list-model")
    dict_llm = LLM(model="dict-model", usage_id="dict-model")
    raw_llm = LLM(model="raw_llm", usage_id="raw_llm")

    new_llm = NewLLM(
        model="new-llm-type",
        usage_id="new-llm-test",
        list_llms=[list_llm],
        dict_llms={"key": dict_llm},
        raw_llm=raw_llm,
    )

    agent = Agent(llm=new_llm)
    llms = list(agent.get_all_llms())

    assert len(llms) == 3
