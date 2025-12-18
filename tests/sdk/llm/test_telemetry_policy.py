from unittest.mock import patch

from litellm.types.llms.openai import ResponsesAPIResponse
from litellm.types.utils import ModelResponse

from openhands.sdk.llm import LLM, Message, TextContent


# Chat path: extra_body policy: always forward if provided, let provider validate


def test_chat_forwards_extra_body_for_all_models():
    llm = LLM(
        model="cerebras/llama-3.3-70b", usage_id="u1", litellm_extra_body={"k": "v"}
    )
    messages = [Message(role="user", content=[TextContent(text="Hi")])]
    with patch("openhands.sdk.llm.llm.litellm_completion") as mock_call:
        mock_call.return_value = ModelResponse(
            id="x",
            choices=[
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            created=0,
            model="cerebras/llama-3.3-70b",
            object="chat.completion",
        )
        llm.completion(messages=messages, metadata={"m": 1})
        mock_call.assert_called_once()
        kwargs = mock_call.call_args[1]
        # extra_body should be forwarded even for non-proxy models
        assert kwargs.get("extra_body") == {"k": "v"}


def test_chat_proxy_forwards_extra_body():
    eb = {"cluster": "c1", "route": "r1"}
    llm = LLM(model="litellm_proxy/gpt-4o", usage_id="u1", litellm_extra_body=eb)
    messages = [Message(role="user", content=[TextContent(text="Hi")])]
    with patch("openhands.sdk.llm.llm.litellm_completion") as mock_call:
        mock_call.return_value = ModelResponse(
            id="x",
            choices=[
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            created=0,
            model="gpt-4o",
            object="chat.completion",
        )
        llm.completion(messages=messages)
        kwargs = mock_call.call_args[1]
        assert kwargs.get("extra_body") == eb


# Responses path: same policy


@patch("openhands.sdk.llm.llm.litellm_responses")
def test_responses_forwards_extra_body_for_all_models(mock_responses):
    llm = LLM(
        model="cerebras/llama-3.3-70b", usage_id="u1", litellm_extra_body={"k": "v"}
    )
    messages = [Message(role="user", content=[TextContent(text="Hi")])]
    mock_responses.return_value = ResponsesAPIResponse(
        id="r1",
        created_at=0,
        output=[],
        parallel_tool_calls=False,
        tool_choice="auto",
        top_p=None,
        tools=[],
        usage=None,
        instructions="",
        status="completed",
    )
    llm.responses(
        messages,
        store=False,
        include=["text.output_text"],
        metadata={"m": 1},
    )
    kwargs = mock_responses.call_args[1]
    # extra_body should be forwarded even for non-proxy models
    assert kwargs.get("extra_body") == {"k": "v"}


@patch("openhands.sdk.llm.llm.litellm_responses")
def test_responses_proxy_forwards_extra_body(mock_responses):
    eb = {"cluster": "c1", "route": "r1"}
    llm = LLM(model="litellm_proxy/gpt-4o", usage_id="u1", litellm_extra_body=eb)
    messages = [Message(role="user", content=[TextContent(text="Hi")])]
    mock_responses.return_value = ResponsesAPIResponse(
        id="r1",
        created_at=0,
        output=[],
        parallel_tool_calls=False,
        tool_choice="auto",
        top_p=None,
        tools=[],
        usage=None,
        instructions="",
        status="completed",
    )
    llm.responses(messages, store=False, include=["text.output_text"])
    kwargs = mock_responses.call_args[1]
    assert kwargs.get("extra_body") == eb
