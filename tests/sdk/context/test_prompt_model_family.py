from openhands.sdk.agent import Agent
from openhands.sdk.llm import LLM


def _make_agent(model: str, **llm_kwargs) -> Agent:
    llm = LLM(model=model, usage_id="test-llm", **llm_kwargs)
    return Agent(llm=llm, tools=[])


def test_system_prompt_includes_openai_model_specific_section() -> None:
    agent = _make_agent("gpt-4o-mini")
    message = agent.system_message
    assert "Model family detected: OpenAI GPT" in message


def test_system_prompt_includes_anthropic_model_specific_section() -> None:
    agent = _make_agent("claude-3-5-sonnet-20241022")
    message = agent.system_message
    assert "Model family detected: Anthropic Claude" in message


def test_system_prompt_includes_google_gemini_section() -> None:
    agent = _make_agent("gemini-2.0-pro")
    message = agent.system_message
    assert "Model family detected: Google Gemini" in message


def test_system_prompt_uses_canonical_name_for_detection() -> None:
    agent = _make_agent("proxy/custom", model_canonical_name="gpt-4o-mini")
    message = agent.system_message
    assert "Model family detected: OpenAI GPT" in message


def test_system_prompt_without_known_family_has_no_model_specific_section() -> None:
    agent = _make_agent("custom-made-model")
    message = agent.system_message
    assert "Model family detected:" not in message
