from unittest.mock import MagicMock, patch

from litellm.types.llms.openai import ResponsesAPIResponse
from litellm.types.utils import ModelResponse

from openhands.sdk.llm import LLM, Message, TextContent


def test_completion_forwards_extra_body_for_proxy_models():
    """Test that litellm_extra_body is forwarded to litellm.completion().

    This applies for proxy models.
    """
    custom_extra_body = {
        "cluster_id": "prod-cluster-1",
        "routing_key": "high-priority",
    }

    llm = LLM(
        model="litellm_proxy/gpt-4o",
        usage_id="test",
        litellm_extra_body=custom_extra_body,
    )
    messages = [Message(role="user", content=[TextContent(text="Hello")])]

    with patch("openhands.sdk.llm.llm.litellm_acompletion") as mock_completion:
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

        llm.completion(messages=messages)

        call_kwargs = mock_completion.call_args[1]
        assert "extra_body" in call_kwargs
        assert call_kwargs["extra_body"] == custom_extra_body


def test_responses_forwards_extra_body_for_all_models():
    """Test that extra_body is forwarded for all models.

    Provider validation occurs downstream. We always forward extra_body if
    provided, regardless of model type. The LLM provider will validate and
    may reject unrecognized parameters.
    """
    custom_extra_body = {
        "guided_json": {"type": "object"},
        "repetition_penalty": 1.1,
    }

    # Test with a non-proxy model (e.g., hosted_vllm)
    llm = LLM(
        model="hosted_vllm/llama-3",
        usage_id="test",
        litellm_extra_body=custom_extra_body,
    )
    messages = [Message(role="user", content=[TextContent(text="Hello")])]

    with patch("openhands.sdk.llm.llm.litellm_aresponses") as mock_responses:
        mock_response = MagicMock(spec=ResponsesAPIResponse)
        mock_response.id = "test-id"
        mock_response.created_at = 1234567890
        mock_response.model = "llama-3"
        mock_response.output = MagicMock()
        mock_response.output.type = "message"
        mock_response.output.message = MagicMock()
        mock_response.output.message.role = "assistant"
        mock_response.output.message.content = [MagicMock(type="text", text="Hello!")]
        mock_response.usage = MagicMock()
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 5
        mock_responses.return_value = mock_response

        llm.responses(messages=messages, include=None, store=False)

        call_kwargs = mock_responses.call_args[1]
        assert "extra_body" in call_kwargs
        assert call_kwargs["extra_body"] == custom_extra_body
