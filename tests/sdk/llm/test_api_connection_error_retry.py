from unittest.mock import patch

import pytest
from litellm.exceptions import APIConnectionError, InternalServerError
from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse, Usage
from pydantic import SecretStr

from openhands.sdk.llm import LLM, LLMResponse, Message, TextContent
from openhands.sdk.llm.exceptions import LLMNoResponseError, LLMServiceUnavailableError


def create_mock_response(content: str = "Test response", response_id: str = "test-id"):
    """Helper function to create properly structured mock responses."""
    return ModelResponse(
        id=response_id,
        choices=[
            Choices(
                finish_reason="stop",
                index=0,
                message=LiteLLMMessage(content=content, role="assistant"),
            )
        ],
        created=1234567890,
        model="gpt-4o",
        object="chat.completion",
        system_fingerprint="test",
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


@pytest.fixture
def default_config():
    return LLM(
        usage_id="test-llm",
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
    )


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_completion_retries_api_connection_error(
    mock_litellm_completion, default_config
):
    """Test that APIConnectionError is properly retried."""
    mock_response = create_mock_response("Retry successful")

    # Mock the litellm_completion to first raise an APIConnectionError,
    # then return a successful response
    mock_litellm_completion.side_effect = [
        APIConnectionError(
            message="API connection error",
            llm_provider="test_provider",
            model="test_model",
        ),
        mock_response,
    ]

    # Create an LLM instance and call completion
    llm = LLM(
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
        usage_id="test-service",
    )
    response = llm.completion(
        messages=[Message(role="user", content=[TextContent(text="Hello!")])],
    )

    # Verify that the retry was successful
    assert isinstance(response, LLMResponse)
    assert response.raw_response == mock_response
    assert mock_litellm_completion.call_count == 2  # Initial call + 1 retry


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_completion_max_retries_api_connection_error(
    mock_litellm_completion, default_config
):
    """Test that APIConnectionError respects max retries and is mapped to SDK error."""
    # Mock the litellm_completion to raise APIConnectionError multiple times
    mock_litellm_completion.side_effect = [
        APIConnectionError(
            message="API connection error 1",
            llm_provider="test_provider",
            model="test_model",
        ),
        APIConnectionError(
            message="API connection error 2",
            llm_provider="test_provider",
            model="test_model",
        ),
        APIConnectionError(
            message="API connection error 3",
            llm_provider="test_provider",
            model="test_model",
        ),
    ]

    # Create an LLM instance and call completion
    llm = LLM(
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
        usage_id="test-service",
    )

    # The completion should raise an SDK typed error after exhausting all retries

    with pytest.raises(LLMServiceUnavailableError) as excinfo:
        llm.completion(
            messages=[Message(role="user", content=[TextContent(text="Hello!")])],
        )

    # Verify that the correct number of retries were attempted
    # The actual behavior is that it tries num_retries times total
    assert mock_litellm_completion.call_count == default_config.num_retries

    # The exception should contain connection error information
    assert "API connection error" in str(excinfo.value)

    # Ensure the original provider exception is preserved as the cause
    assert isinstance(excinfo.value.__cause__, APIConnectionError)


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_completion_no_retry_on_success(mock_litellm_completion, default_config):
    """Test that successful calls don't trigger retries."""
    mock_response = create_mock_response("Success on first try")
    mock_litellm_completion.return_value = mock_response

    # Create an LLM instance and call completion
    llm = LLM(
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
        usage_id="test-service",
    )
    response = llm.completion(
        messages=[Message(role="user", content=[TextContent(text="Hello!")])],
    )

    # Verify that no retries were needed
    assert isinstance(response, LLMResponse)
    assert response.raw_response == mock_response
    assert mock_litellm_completion.call_count == 1  # Only the initial call


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_completion_no_retry_on_non_retryable_error(
    mock_litellm_completion, default_config
):
    """Test that non-retryable errors don't trigger retries."""
    # Mock a non-retryable error (e.g., ValueError)
    mock_litellm_completion.side_effect = ValueError("Invalid input")

    # Create an LLM instance and call completion
    llm = LLM(
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
        usage_id="test-service",
    )

    # The completion should raise the ValueError immediately without retries
    with pytest.raises(ValueError) as excinfo:
        llm.completion(
            messages=[Message(role="user", content=[TextContent(text="Hello!")])],
        )

    # Verify that no retries were attempted
    assert mock_litellm_completion.call_count == 1  # Only the initial call
    assert "Invalid input" in str(excinfo.value)


def test_retry_configuration_validation():
    """Test that retry configuration is properly validated."""
    # Test with zero retries
    llm_no_retry = LLM(
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        num_retries=0,
        usage_id="test-llm",
    )
    assert llm_no_retry.num_retries == 0

    # Test with custom retry settings
    llm_custom = LLM(
        usage_id="test-llm",
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        num_retries=5,
        retry_min_wait=2,
        retry_max_wait=10,
        retry_multiplier=2.0,
    )
    assert llm_custom.num_retries == 5
    assert llm_custom.retry_min_wait == 2
    assert llm_custom.retry_max_wait == 10
    assert llm_custom.retry_multiplier == 2.0


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_retry_listener_callback(mock_litellm_completion, default_config):
    """Test that retry listener callback is called during retries."""
    retry_calls = []

    def retry_listener(attempt: int, max_attempts: int):
        retry_calls.append((attempt, max_attempts))

    mock_response = create_mock_response("Success after retry")

    mock_litellm_completion.side_effect = [
        APIConnectionError(
            message="Connection failed",
            llm_provider="test_provider",
            model="test_model",
        ),
        mock_response,
    ]

    # Create an LLM instance with retry listener
    llm = LLM(
        usage_id="test-llm",
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
        retry_listener=retry_listener,
    )
    response = llm.completion(
        messages=[Message(role="user", content=[TextContent(text="Hello!")])],
    )

    # Verify that the retry listener was called
    assert isinstance(response, LLMResponse)
    assert response.raw_response == mock_response
    assert len(retry_calls) >= 1  # At least one retry attempt should be logged

    # Check that retry listener received correct parameters
    if retry_calls:
        attempt, max_attempts = retry_calls[0]
        assert isinstance(attempt, int)
        assert isinstance(max_attempts, int)
        assert attempt >= 1
        assert max_attempts == default_config.num_retries


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_internal_server_error_choices_none_retries_with_temperature_bump(
    mock_litellm_completion, default_config
):
    """
    Test that InternalServerError from convert_to_model_response_object
    is converted to LLMNoResponseError and retried with temperature bump.
    """
    # Ensure we start at 0.0 to trigger bump to 1.0 on retry
    assert default_config.temperature == 0.0

    mock_litellm_completion.side_effect = [
        InternalServerError(
            message=(
                "Invalid response object Traceback (most recent call last):\n"
                '  File "litellm/litellm_core_utils/llm_response_utils/'
                'convert_dict_to_response.py", line 466, in '
                "convert_to_model_response_object\n"
                '    assert response_object["choices"] is not None\n'
                "AssertionError"
            ),
            llm_provider="test_provider",
            model="test_model",
        ),
        create_mock_response("success"),
    ]

    response = default_config.completion(
        messages=[Message(role="user", content=[TextContent(text="hi")])]
    )

    assert isinstance(response, LLMResponse)
    assert response.message is not None
    assert mock_litellm_completion.call_count == 2

    # Verify that on the second call, temperature was bumped to 1.0
    _, second_kwargs = mock_litellm_completion.call_args_list[1]
    assert second_kwargs.get("temperature") == 1.0


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_internal_server_error_choices_none_exhausts_retries(
    mock_litellm_completion, default_config
):
    """
    Test that when all retries fail with InternalServerError from
    convert_to_model_response_object, LLMNoResponseError is raised.
    """
    mock_litellm_completion.side_effect = [
        InternalServerError(
            message=(
                "File convert_to_model_response_object: "
                "assert response_object['choices'] is not None"
            ),
            llm_provider="test_provider",
            model="test_model",
        ),
        InternalServerError(
            message=(
                "File convert_to_model_response_object: "
                "assert response_object['choices'] is not None"
            ),
            llm_provider="test_provider",
            model="test_model",
        ),
    ]

    with pytest.raises(LLMNoResponseError) as excinfo:
        default_config.completion(
            messages=[Message(role="user", content=[TextContent(text="hi")])]
        )

    assert mock_litellm_completion.call_count == default_config.num_retries
    assert "malformed response" in str(excinfo.value).lower()


@patch("openhands.sdk.llm.llm.litellm_completion")
def test_internal_server_error_unrelated_not_converted(
    mock_litellm_completion, default_config
):
    """
    Test that unrelated InternalServerError (not about choices) is NOT
    converted to LLMNoResponseError.
    """
    mock_litellm_completion.side_effect = [
        InternalServerError(
            message="Database connection failed",
            llm_provider="test_provider",
            model="test_model",
        ),
        InternalServerError(
            message="Database connection failed",
            llm_provider="test_provider",
            model="test_model",
        ),
    ]

    # Should raise InternalServerError (mapped to LLMServiceUnavailableError),
    # not LLMNoResponseError
    with pytest.raises(Exception) as excinfo:
        default_config.completion(
            messages=[Message(role="user", content=[TextContent(text="hi")])]
        )

    # Should NOT be LLMNoResponseError
    assert not isinstance(excinfo.value, LLMNoResponseError)
    assert mock_litellm_completion.call_count == default_config.num_retries
