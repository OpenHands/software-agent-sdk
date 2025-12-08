from openhands.sdk.agent import Agent
from openhands.sdk.llm import LLM


def _make_agent(model: str, **llm_kwargs) -> Agent:
    llm = LLM(model=model, usage_id="test-llm", **llm_kwargs)
    return Agent(llm=llm, tools=[])


def test_system_prompt_includes_openai_gpt_5_model_specific_section() -> None:
    agent = _make_agent("gpt-5")
    message = agent.system_message
    assert "Model family detected: OpenAI GPT" in message
    assert "Variant detected: OpenAI GPT-5 series" in message


def test_system_prompt_includes_openai_gpt_5_codex_model_specific_section() -> None:
    agent = _make_agent("gpt-5-codex")
    message = agent.system_message
    assert "Model family detected: OpenAI GPT" in message
    assert "Variant detected: OpenAI GPT-5 Codex" in message


def test_system_prompt_uses_canonical_name_for_detection() -> None:
    agent = _make_agent("proxy/custom", model_canonical_name="gpt-4o-mini")
    message = agent.system_message
    assert "Model family detected: OpenAI GPT" in message


def test_system_prompt_respects_model_variant_override() -> None:
    llm = LLM(model="gpt-5-codex", usage_id="test-llm")
    agent = Agent(llm=llm, tools=[], system_prompt_kwargs={"model_variant": "gpt-5"})
    message = agent.system_message
    assert "Variant detected: OpenAI GPT-5 series" in message


def test_system_prompt_without_known_family_has_no_model_specific_section() -> None:
    agent = _make_agent("custom-made-model")
    message = agent.system_message
    assert "Model family detected:" not in message
    assert "Variant detected:" not in message
