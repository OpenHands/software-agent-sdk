import json
from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr

from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm.exceptions import LLMNoResponseError, LLMResponseRefusedError


def provider_reply(request: httpx.Request, reason: str) -> httpx.Response:
    assert request.url.path == "/v1/responses"
    output = []
    if reason == "refusal":
        # A refusal must win even when an earlier tool call cannot be parsed.
        output.append(
            {
                "type": "function_call",
                "id": "fc-1",
                "call_id": "call-1",
                "name": "",
                "arguments": "{",
            }
        )
        output.append(
            {
                "id": "msg-1",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": "partial", "annotations": []},
                    {"type": "refusal", "refusal": "Declined."},
                ],
            }
        )
    status = "completed" if output else "incomplete"
    payload = {
        "id": "resp-1",
        "object": "response",
        "created_at": 1,
        "model": "gpt-4o",
        "status": status,
        "incomplete_details": None if output else {"reason": reason},
        "output": output,
        "usage": {"input_tokens": 17, "output_tokens": 2, "total_tokens": 19},
    }
    if not json.loads(request.content).get("stream"):
        return httpx.Response(200, request=request, json=payload)
    event = {"type": f"response.{status}", "response": payload, "sequence_number": 0}
    return httpx.Response(
        200,
        request=request,
        headers={"Content-Type": "text/event-stream"},
        content=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n",
    )


@pytest.fixture
def llm() -> LLM:
    return LLM(
        model="openai/gpt-4o",
        api_key=SecretStr("test-key"),
        base_url="http://127.0.0.1:1/v1",
        num_retries=2,
        retry_min_wait=0,
        retry_max_wait=0,
    )


@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("stream", [False, True], ids=["json", "stream"])
@pytest.mark.parametrize("reason", ["content_filter", "refusal"])
async def test_responses_refusal_is_terminal(llm: LLM, use_async, stream, reason):
    client = httpx.AsyncClient if use_async else httpx.Client
    messages = [
        Message(role="system", content=[TextContent(text="Answer briefly.")]),
        Message(role="user", content=[TextContent(text="Hello")]),
    ]
    with patch.object(
        client,
        "send",
        side_effect=lambda request, **kw: provider_reply(request, reason),
    ) as send:
        with pytest.raises(LLMResponseRefusedError) as exc:
            if use_async:
                await llm.aresponses(messages, stream=stream, on_token=lambda _: None)
            else:
                llm.responses(messages, stream=stream, on_token=lambda _: None)
    assert send.call_count == 1
    response_id = llm.metrics.token_usages[0].response_id
    assert response_id
    assert exc.value.response_id == response_id
    assert response_id in str(exc.value)
    usage = llm.metrics.accumulated_token_usage
    assert usage is not None
    assert (usage.prompt_tokens, usage.completion_tokens) == (17, 2)


@pytest.mark.parametrize("stream", [False, True], ids=["json", "stream"])
def test_responses_token_limit_is_not_a_refusal(llm: LLM, stream):
    messages = [Message(role="system", content=[TextContent(text="Say hello.")])]
    with patch.object(
        httpx.Client,
        "send",
        side_effect=lambda request, **kw: provider_reply(request, "max_output_tokens"),
    ) as send:
        if stream:
            with pytest.raises(LLMNoResponseError):
                llm.responses(messages, stream=True, on_token=lambda _: None)
            assert send.call_count == llm.num_retries
        else:
            response = llm.responses(messages)
            assert response.message.content == []
            assert send.call_count == 1
