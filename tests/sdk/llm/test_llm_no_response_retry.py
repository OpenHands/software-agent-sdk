from unittest.mock import patch

import pytest
from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse, Usage
from pydantic import SecretStr

from openhands.sdk.llm import LLM, LLMResponse, Message, TextContent
from openhands.sdk.llm.exceptions import LLMNoResponseError


def create_mock_response(
    content: str = "ok", response_id: str = "r-1"
) -> ModelResponse:
    return ModelResponse(
        id=response_id,
        choices=[
            Choices(
                finish_reason="stop",
                index=0,
                message=LiteLLMMessage(content=content, role="assistant"),
            )
        ],
        created=1,
        model="gpt-4o",
        object="chat.completion",
        system_fingerprint="t",
        usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def create_empty_choices_response(response_id: str = "empty-1") -> ModelResponse:
    return ModelResponse(
        id=response_id,
        choices=[],  # triggers LLMNoResponseError inside retry boundary
        created=1,
        model="gpt-4o",
        object="chat.completion",
        usage=Usage(prompt_tokens=1, completion_tokens=0, total_tokens=1),
    )


@pytest.fixture
def base_llm() -> LLM:
    return LLM(
        usage_id="test-llm",
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
        temperature=0.0,  # Explicitly set to test temperature bump behavior
    )


@patch("openhands.sdk.llm.llm.litellm_acompletion")
def test_no_response_retries_then_succeeds(mock_completion, base_llm: LLM) -> None:
    mock_completion.side_effect = [
        create_empty_choices_response("empty-1"),
        create_mock_response("success"),
    ]

    resp = base_llm.completion(
        messages=[Message(role="user", content=[TextContent(text="hi")])]
    )

    assert isinstance(resp, LLMResponse)
    assert resp.message is not None
    assert mock_completion.call_count == 2  # initial + 1 retry


@patch("openhands.sdk.llm.llm.litellm_acompletion")
def test_no_response_exhausts_retries_bubbles_llm_no_response(
    mock_completion, base_llm: LLM
) -> None:
    # Always return empty choices -> keeps raising LLMNoResponseError inside retry
    mock_completion.side_effect = [
        create_empty_choices_response("empty-1"),
        create_empty_choices_response("empty-2"),
    ]

    with pytest.raises(LLMNoResponseError):
        base_llm.completion(
            messages=[Message(role="user", content=[TextContent(text="hi")])]
        )

    # Tenacity runs function num_retries times total
    assert mock_completion.call_count == base_llm.num_retries


@patch("openhands.sdk.llm.llm.litellm_acompletion")
def test_no_response_retry_bumps_temperature(mock_completion, base_llm: LLM) -> None:
    # Ensure we start at 0.0 to trigger bump to 1.0 on retry
    assert base_llm.temperature == 0.0

    mock_completion.side_effect = [
        create_empty_choices_response("empty-1"),
        create_mock_response("ok"),
    ]

    base_llm.completion(
        messages=[Message(role="user", content=[TextContent(text="hi")])]
    )

    # Verify that on the second call, temperature was bumped to 1.0 by RetryMixin
    assert mock_completion.call_count == 2
    # Grab kwargs from the second call
    _, second_kwargs = mock_completion.call_args_list[1]
    assert second_kwargs.get("temperature") == 1.0
