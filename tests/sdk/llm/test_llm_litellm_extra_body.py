from unittest.mock import patch

from litellm.types.utils import ModelResponse

from openhands.sdk.llm import LLM, Message, TextContent


def test_litellm_extra_body_passed_to_completion():
    """Test that litellm_extra_body is correctly passed to litellm.completion()."""
    custom_extra_body = {
        "cluster_id": "prod-cluster-1",
        "routing_key": "high-priority",
        "user_tier": "premium",
        "custom_headers": {
            "X-Request-Source": "openhands-agent",
        },
    }

    llm = LLM(
        model="litellm_proxy/gpt-4o",
        usage_id="test",
        litellm_extra_body=custom_extra_body,
    )
    messages = [Message(role="user", content=[TextContent(text="Hello")])]

    with patch("openhands.sdk.llm.llm.litellm_completion") as mock_completion:
        # Create a proper ModelResponse mock
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

        # Call completion
        llm.completion(messages=messages)

        # Verify that litellm.completion was called with our extra_body
        mock_completion.assert_called_once()
        call_kwargs = mock_completion.call_args[1]

        # Check that extra_body was passed correctly
        assert "extra_body" in call_kwargs
        assert call_kwargs["extra_body"] == custom_extra_body

        # Verify specific custom fields were passed through
        assert call_kwargs["extra_body"]["cluster_id"] == "prod-cluster-1"
        assert call_kwargs["extra_body"]["routing_key"] == "high-priority"
        assert (
            call_kwargs["extra_body"]["custom_headers"]["X-Request-Source"]
            == "openhands-agent"
        )
