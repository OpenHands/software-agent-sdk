from unittest.mock import patch

from litellm.types.utils import ModelResponse

from openhands.sdk.llm import LLM, Message, TextContent


def test_metadata_filtered_for_non_proxy_providers():
    """Test that metadata is filtered out for non-litellm_proxy providers."""
    llm = LLM(model="cerebras/llama-3.3-70b", usage_id="test")
    messages = [Message(role="user", content=[TextContent(text="Hello")])]

    with patch("openhands.sdk.llm.llm.litellm_completion") as mock_completion:
        mock_response = ModelResponse(
            id="test-id",
            choices=[
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello!"},
                    "finish_reason": "stop",
                }
            ],
            created=1234567890,
            model="cerebras/llama-3.3-70b",
            object="chat.completion",
        )
        mock_completion.return_value = mock_response

        # Call completion with metadata in kwargs
        llm.completion(messages=messages, metadata={"session_id": "test-session"})

        # Verify that litellm.completion was called
        mock_completion.assert_called_once()
        call_kwargs = mock_completion.call_args[1]

        # Check that metadata was filtered out (not passed to cerebras)
        assert "metadata" not in call_kwargs


def test_metadata_filtered_for_litellm_proxy_too():
    """We NEVER send metadata, even for litellm_proxy providers."""
    llm = LLM(
        model="litellm_proxy/gpt-4o",
        usage_id="test",
        base_url="http://localhost:4000",
    )
    messages = [Message(role="user", content=[TextContent(text="Hello")])]

    with patch("openhands.sdk.llm.llm.litellm_completion") as mock_completion:
        mock_response = ModelResponse(
            id="test-id",
            choices=[
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello!"},
                    "finish_reason": "stop",
                }
            ],
            created=1234567890,
            model="gpt-4o",
            object="chat.completion",
        )
        mock_completion.return_value = mock_response

        # Call completion with metadata in kwargs
        test_metadata = {"session_id": "test-session", "user_id": "user-123"}
        llm.completion(messages=messages, metadata=test_metadata)

        # Verify that litellm.completion was called
        mock_completion.assert_called_once()
        call_kwargs = mock_completion.call_args[1]

        # Check that metadata is NOT forwarded, even for litellm_proxy
        assert "metadata" not in call_kwargs


def test_extra_body_filtered_for_non_proxy_providers():
    """Test that extra_body is also filtered out for non-proxy providers."""
    llm = LLM(model="cerebras/llama-3.3-70b", usage_id="test")
    messages = [Message(role="user", content=[TextContent(text="Hello")])]

    messages = [Message(role="user", content=[TextContent(text="Hello")])]

    with patch("openhands.sdk.llm.llm.litellm_completion") as mock_completion:
        mock_response = ModelResponse(
            id="test-id",
            choices=[
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello!"},
                    "finish_reason": "stop",
                }
            ],
            created=1234567890,
            model="cerebras/llama-3.3-70b",
            object="chat.completion",
        )
        mock_completion.return_value = mock_response

        # Call completion with extra_body and metadata in kwargs
        llm.completion(
            messages=messages,
            extra_body={"custom_field": "value"},
            metadata={"session_id": "test-session"},
        )

        # Verify that litellm.completion was called
        mock_completion.assert_called_once()
        call_kwargs = mock_completion.call_args[1]

        # Check that both extra_body and metadata were filtered out
        assert "extra_body" not in call_kwargs
        assert "metadata" not in call_kwargs
