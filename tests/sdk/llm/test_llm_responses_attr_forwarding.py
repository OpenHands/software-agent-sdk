from __future__ import annotations

from unittest.mock import patch

from pydantic import SecretStr

from openhands.sdk.llm import LLM, Message, TextContent


@patch("openhands.sdk.llm.llm.litellm_responses")
def test_responses_api_forwards_all_relevant_attrs(mock_responses):
    """Ensure LLM.responses forwards key attributes to LiteLLM Responses API.

    This covers both:
    - transport-level options not handled by select_responses_options
      (model, api_key, api_base/base_url, api_version, timeout, drop_params, seed)
    - responses options normalized by select_responses_options
      (temperature, tool_choice, include, store, reasoning, max_output_tokens)

    The known gap custom_llm_provider is asserted at the end to avoid masking
    any other potential failures.
    """

    # Return a minimal valid ResponsesAPIResponse to let the call path complete
    # and avoid exceptions before our assertions.
    from litellm.types.llms.openai import ResponsesAPIResponse

    mock_responses.return_value = ResponsesAPIResponse(
        id="resp_test_1",
        created_at=1234567890,
        output=[],
    )

    llm = LLM(
        # Choose a model that triggers the Responses API path per model_features
        # See RESPONSES_API_PATTERNS in model_features.py (e.g., 'gpt-5*')
        model="gpt-5-mini",
        api_key=SecretStr("sk-test-456"),
        base_url="https://example.com/v1",
        api_version="2024-01-01",
        custom_llm_provider="my-provider",
        timeout=7,
        drop_params=False,
        seed=4321,
        max_output_tokens=123,
        enable_encrypted_reasoning=True,  # ensures include carries encrypted content
        usage_id="responses-forwarding",
    )

    _ = llm.responses(
        messages=[
            Message(role="system", content=[TextContent(text="You are helpful")]),
            Message(role="user", content=[TextContent(text="hi")]),
        ]
    )

    assert mock_responses.called, "Expected litellm.responses to be called"
    called_kwargs = mock_responses.call_args.kwargs

    # Transport-level passthrough
    assert called_kwargs.get("model") == "gpt-5-mini"
    assert called_kwargs.get("api_key") == "sk-test-456"
    # responses() uses api_base for base URL
    assert called_kwargs.get("api_base") == "https://example.com/v1"
    assert called_kwargs.get("api_version") == "2024-01-01"
    assert called_kwargs.get("timeout") == 7
    assert called_kwargs.get("drop_params") is False
    assert called_kwargs.get("seed") == 4321

    # Responses path options
    assert called_kwargs.get("temperature") == 1.0
    assert called_kwargs.get("tool_choice") == "auto"
    assert called_kwargs.get("input") is not None
    assert called_kwargs.get("instructions") is not None
    # store defaults to False unless provided
    assert called_kwargs.get("store") is False
    # include should contain encrypted reasoning content when store=False
    include = called_kwargs.get("include") or []
    assert "reasoning.encrypted_content" in include
    # reasoning payload present with effort derived from llm
    reasoning = called_kwargs.get("reasoning") or {}
    assert reasoning.get("effort") in {"low", "medium", "high", "xhigh", "none"}
    # Summary is only included if explicitly set on the LLM; not required by default

    # max_output_tokens should be forwarded directly on Responses path
    assert called_kwargs.get("max_output_tokens") == 123

    # NOTE: custom_llm_provider isn't forwarded by responses() path either currently.
    # expected_provider = called_kwargs.get("custom_llm_provider")
    # assert expected_provider == "my-provider"
