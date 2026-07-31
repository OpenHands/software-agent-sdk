from openhands.sdk.llm.utils.litellm_provider import LLMProvider


def test_llm_provider_parses_nested_openrouter_model():
    provider = LLMProvider.from_model(
        model="openrouter/anthropic/claude-sonnet-4", api_base=None
    )

    assert provider.name == "openrouter"
    assert provider.model == "anthropic/claude-sonnet-4"
    assert provider.as_litellm_call_kwargs() == {
        "model": "anthropic/claude-sonnet-4",
        "custom_llm_provider": "openrouter",
    }


def test_llm_provider_parses_bedrock_model():
    provider = LLMProvider.from_model(
        model="bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
        api_base=None,
    )

    assert provider.name == "bedrock"
    assert provider.is_bedrock is True
    assert provider.model == "anthropic.claude-3-5-sonnet-20241022-v2:0"


def test_llm_provider_strips_api_key_for_bedrock_calls():
    provider = LLMProvider.from_model(
        model="bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
        api_base=None,
    )

    assert provider.api_key_for_litellm("sk-ant-not-a-bedrock-key") is None
    assert provider.as_litellm_call_kwargs(api_key="sk-ant-not-a-bedrock-key") == {
        "model": "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "custom_llm_provider": "bedrock",
    }


def test_llm_provider_handles_unknown_model_without_provider():
    provider = LLMProvider.from_model(model="unknown-model", api_base=None)

    assert provider.name is None
    assert provider.model == "unknown-model"
    assert provider.as_litellm_call_kwargs() == {"model": "unknown-model"}
