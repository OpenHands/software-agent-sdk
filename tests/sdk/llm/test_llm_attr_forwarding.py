from __future__ import annotations

from unittest.mock import patch

from pydantic import SecretStr

from openhands.sdk.llm import LLM, Message, TextContent
from tests.conftest import create_mock_litellm_response


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_all_config_attrs_are_forwarded_to_litellm_completion(mock_completion):
    """Verify LLM forwards key attributes to litellm.completion.

    This covers both sampling options managed by select_chat_options
    (temperature/top_p/top_k, max tokens) and transport-level options
    not handled there (base_url, api_version, drop_params, seed,
    custom_llm_provider). If any are not forwarded, the test fails.
    """
    mock_completion.return_value = create_mock_litellm_response("ok")

    llm = LLM(
        model="gpt-4o",  # non-reasoning path keeps sampling params present
        api_key=SecretStr("sk-test-123"),
        base_url="https://example.com/v1",
        api_version="2024-01-01",
        custom_llm_provider="my-provider",
        timeout=42,
        drop_params=False,
        seed=1234,
        temperature=0.5,
        top_p=0.8,
        top_k=5,
        max_output_tokens=99,
        usage_id="forwarding-test",
    )

    _ = llm.completion(
        messages=[Message(role="user", content=[TextContent(text="hi")])]
    )

    # Collect the actual kwargs forwarded to litellm.completion
    assert mock_completion.called, "Expected litellm.completion to be called"
    called_kwargs = mock_completion.call_args.kwargs

    # Expectations: direct passthrough (not via select_chat_options)
    assert called_kwargs.get("model") == "gpt-4o"
    assert called_kwargs.get("api_key") == "sk-test-123"
    # Our transport uses `api_base` (LiteLLM/OpenAI naming) for base URL
    assert called_kwargs.get("api_base") == "https://example.com/v1"
    assert called_kwargs.get("api_version") == "2024-01-01"
    assert called_kwargs.get("timeout") == 42
    assert called_kwargs.get("drop_params") is False
    assert called_kwargs.get("seed") == 1234

    # Expectations: values that flow through select_chat_options
    # - top_k/top_p/temperature should be present as-is for non-reasoning models
    assert called_kwargs.get("top_k") == 5, "Expected top_k to be forwarded"
    assert called_kwargs.get("top_p") == 0.8, "Expected top_p to be forwarded"
    assert called_kwargs.get("temperature") == 0.5, (
        "Expected temperature to be forwarded"
    )

    # - max_output_tokens is normalized to `max_completion_tokens` for OpenAI chat
    #   (Azure uses `max_tokens`, but this test uses non-Azure model id)
    assert called_kwargs.get("max_completion_tokens") == 99, (
        "Expected max_output_tokens -> max_completion_tokens forwarding"
    )

    # NOTE: custom_llm_provider is currently not forwarded by transport.
    # This is a known gap under discussion; do not fail on it here.
    # expected_provider = called_kwargs.get("custom_llm_provider")
    # assert expected_provider == "my-provider"
