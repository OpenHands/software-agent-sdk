from openhands.sdk.llm.utils.litellm_provider import LLMProvider


def test_llm_provider_parses_nested_openrouter_model():
    provider = LLMProvider.from_model(
        model="openrouter/anthropic/claude-sonnet-4", api_base=None
    )

    assert provider.name == "openrouter"
    assert provider.model == "anthropic/claude-sonnet-4"
    assert provider.canonical_name == "openrouter/anthropic/claude-sonnet-4"
    assert not hasattr(provider, "requested_model")
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
    assert (
        provider.canonical_name == "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0"
    )


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


def test_llm_provider_reuses_cached_instance_for_same_request():
    provider = LLMProvider.from_model(model="gpt-4o", api_base=None)

    cached_provider, cached_key = LLMProvider.resolve_cached(
        model="gpt-4o",
        api_base=None,
        cached_provider=provider,
        cached_key=LLMProvider.cache_key(model="gpt-4o", api_base=None),
    )

    assert cached_provider is provider
    assert cached_key == ("gpt-4o", None)


def test_llm_provider_refreshes_cached_instance_when_request_changes():
    provider = LLMProvider.from_model(model="gpt-4o", api_base=None)

    refreshed_provider, refreshed_key = LLMProvider.resolve_cached(
        model="proxy/test-renamed-model",
        api_base="http://localhost:8000",
        cached_provider=provider,
        cached_key=LLMProvider.cache_key(model="gpt-4o", api_base=None),
    )

    assert refreshed_provider is not provider
    assert refreshed_key == ("proxy/test-renamed-model", "http://localhost:8000")


def test_llm_provider_handles_unknown_model_without_provider():
    provider = LLMProvider.from_model(model="unknown-model", api_base=None)

    assert provider.name is None
    assert provider.provider_enum is None
    assert provider.model == "unknown-model"
    assert provider.canonical_name == "unknown-model"
    assert provider.model_info is None
    assert provider.as_litellm_call_kwargs() == {"model": "unknown-model"}


def test_llm_provider_infers_api_base_from_model_info():
    provider = LLMProvider.from_model(
        model="anthropic/claude-sonnet-4-5-20250929", api_base=None
    )

    assert provider.infer_api_base() == "https://api.anthropic.com"


def test_llm_provider_infers_api_base_from_litellm_defaults():
    provider = LLMProvider.from_model(
        model="mistral/mistral-large-latest", api_base=None
    )

    assert provider.infer_api_base() == "https://api.mistral.ai/v1"
