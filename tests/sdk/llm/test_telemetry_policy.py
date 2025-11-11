from unittest.mock import patch

from litellm.types.llms.openai import ResponsesAPIResponse
from litellm.types.utils import ModelResponse

from openhands.sdk.llm import LLM, Message, TextContent


# Chat path: only extra_body policy: forward only for litellm_proxy, drop otherwise


def test_chat_non_proxy_drops_extra_body():
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
        llm.completion(messages=messages, extra_body={"x": 1}, metadata={"m": 1})
        mock_call.assert_called_once()
        kwargs = mock_call.call_args[1]
        assert "extra_body" not in kwargs


def test_chat_proxy_forwards_extra_body_only():
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
def test_responses_non_proxy_drops_extra_body(mock_responses):
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
        extra_body={"x": 1},
        metadata={"m": 1},
    )
    kwargs = mock_responses.call_args[1]
    assert "extra_body" not in kwargs


@patch("openhands.sdk.llm.llm.litellm_responses")
def test_responses_proxy_forwards_extra_body_only(mock_responses):
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
