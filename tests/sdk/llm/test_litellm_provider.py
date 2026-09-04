import litellm
import pytest

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


def test_llm_provider_keeps_requested_api_base_verbatim():
    # LiteLLM's own resolution appends "/v1" to a custom mistral base; the
    # helper must not leak that mutated value back into the forwarded kwargs.
    provider = LLMProvider.from_model(
        model="mistral/mistral-small-latest",
        api_base="https://myproxy.example.com",
    )

    assert provider.api_base == "https://myproxy.example.com"


def test_llm_provider_preserves_namespaced_model_after_litellm_reparse():
    # LiteLLM strips the first "openai/" prefix from "openai/openai/example-
    # model", leaving parsed model "openai/example-model" with provider
    # "openai". Without re-prefixing, LiteLLM's Responses API path strips the
    # "openai/" prefix again and sends "example-model" upstream (400 Model not
    # found). The kwargs from as_litellm_call_kwargs must resolve back to the
    # parsed model, not the double-stripped one.
    provider = LLMProvider.from_model(
        model="openai/openai/example-model",
        api_base="https://myproxy.example.com/v1",
    )

    assert provider.name == "openai"
    assert provider.model == "openai/example-model"

    kwargs = provider.as_litellm_call_kwargs()
    assert kwargs["custom_llm_provider"] == "openai"

    resolved = litellm.get_llm_provider(
        model=kwargs["model"],
        custom_llm_provider=kwargs["custom_llm_provider"],
        api_base=provider.api_base,
        api_key=None,
    )
    assert resolved[0] == "openai/example-model"
    assert resolved[1] == "openai"


@pytest.mark.parametrize(
    ("model", "api_base"),
    [
        ("gpt-4o", None),
        ("anthropic/claude-3-5-haiku-20241022", None),
        ("openrouter/anthropic/claude-sonnet-4", None),
        ("mistral/mistral-small-latest", "https://myproxy.example.com"),
        ("litellm_proxy/claude-sonnet-4", "https://llm-proxy.app.all-hands.dev"),
        ("openai/local-model", "http://localhost:8000/v1"),
    ],
)
def test_split_kwargs_equivalent_to_full_model_string(model: str, api_base: str | None):
    """The refactor sends parsed model + custom_llm_provider instead of the
    full "provider/model" string. Prove LiteLLM resolves both forms to the
    same provider/model/api_base, i.e. the wire behavior is unchanged.
    """
    provider = LLMProvider.from_model(model=model, api_base=api_base)

    full = litellm.get_llm_provider(
        model=model, custom_llm_provider=None, api_base=api_base, api_key=None
    )
    split = litellm.get_llm_provider(
        model=provider.model,
        custom_llm_provider=provider.name,
        api_base=provider.api_base,
        api_key=None,
    )

    assert (split[0], split[1], split[3]) == (full[0], full[1], full[3])
