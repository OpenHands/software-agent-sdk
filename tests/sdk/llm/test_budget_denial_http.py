import json
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from pydantic import SecretStr

import openhands.sdk.llm.llm as llm_module
from openhands.sdk import Agent, Conversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.llm import LLM, FallbackStrategy, Message, TextContent
from openhands.sdk.llm.call_context import LLMCallContext
from openhands.sdk.llm.cost_budget import CostBudget
from openhands.sdk.llm.exceptions import (
    LLMBudgetExceededError,
    LLMRateLimitError,
    is_quota_exhaustion_error,
)
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from tests.conftest import create_mock_litellm_response


@pytest.fixture
def provider():
    replies = deque()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            status, body = replies.popleft()
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", replies, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize(
    "model", ["anthropic/claude-sonnet-4-5", "gemini/gemini-2.5-pro"]
)
@pytest.mark.parametrize("use_async", [False, True], ids=["run", "arun"])
async def test_optional_search_prices_allow_ordinary_conversation_over_http(
    provider, tmp_path, monkeypatch, model, use_async
):
    base_url, replies, requests = provider
    fixture = (
        Path(__file__).with_name("fixtures") / "optional-search-provider-info.json"
    )
    key = "claude-sonnet-4-5" if model.startswith("anthropic/") else model
    info = json.loads(fixture.read_text())["model_info"][key]
    monkeypatch.setattr(llm_module, "get_litellm_model_info", lambda **_: info)
    if model.startswith("anthropic/"):
        response = {
            "id": "message-budget",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-5",
            "content": [{"type": "text", "text": "done"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 5},
        }
    else:
        response = {
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": "done"}]},
                    "finishReason": "STOP",
                    "index": 0,
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 100,
                "candidatesTokenCount": 5,
                "totalTokenCount": 105,
            },
            "modelVersion": "gemini-2.5-pro",
        }
    replies.append((200, response))
    conv = Conversation(
        agent=Agent(
            llm=LLM(
                model=model,
                base_url=base_url,
                api_key=SecretStr("fixture-key"),
                num_retries=0,
                max_output_tokens=100,
            ),
            tools=[],
        ),
        workspace=str(tmp_path),
        persistence_dir=str(tmp_path / "persist"),
        max_budget_per_run=50,
        visualizer=None,
    )
    try:
        conv.send_message("Reply briefly.")
        if use_async:
            await conv.arun()
        else:
            conv.run()

        assert conv.state.execution_status == ConversationExecutionStatus.FINISHED
        assert len(requests) == 1
        assert conv.conversation_stats.get_combined_metrics().accumulated_cost > 0
    finally:
        conv.close()


@pytest.mark.parametrize(
    "model,fixture_key",
    [
        ("openai/gpt-5.3-codex", "gpt-5.3-codex"),
        ("openhands/gpt-5.3-codex", "gpt-5.3-codex"),
        ("bedrock_mantle/openai.gpt-5.5", "bedrock_mantle/openai.gpt-5.5"),
    ],
)
@pytest.mark.parametrize("use_async", [False, True], ids=["run", "arun"])
async def test_optional_search_prices_allow_responses_conversation_over_http(
    provider, tmp_path, monkeypatch, model, fixture_key, use_async
):
    base_url, replies, requests = provider
    fixture = (
        Path(__file__).with_name("fixtures") / "optional-search-provider-info.json"
    )
    info = json.loads(fixture.read_text())["model_info"][fixture_key]
    monkeypatch.setattr(llm_module, "get_litellm_model_info", lambda **_: info)
    replies.append(
        (
            200,
            {
                "id": "resp-budget-codex",
                "object": "response",
                "created_at": 1,
                "model": "gpt-5.3-codex",
                "status": "completed",
                "parallel_tool_calls": False,
                "tool_choice": "auto",
                "tools": [],
                "output": [
                    {
                        "type": "message",
                        "id": "msg-budget-codex",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": "done", "annotations": []}
                        ],
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 5, "total_tokens": 105},
            },
        )
    )
    conv = Conversation(
        agent=Agent(
            llm=LLM(
                model=model,
                base_url=base_url,
                api_key=SecretStr("fixture-key"),
                num_retries=0,
                max_output_tokens=100,
            ),
            tools=[],
        ),
        workspace=str(tmp_path),
        persistence_dir=str(tmp_path / "persist"),
        max_budget_per_run=50,
        visualizer=None,
    )
    try:
        conv.send_message("Reply briefly.")
        if use_async:
            await conv.arun()
        else:
            conv.run()

        assert conv.state.execution_status == ConversationExecutionStatus.FINISHED
        assert len(requests) == 1
        assert requests[0]["input"]
        assert requests[0]["max_output_tokens"] == 100
        assert conv.conversation_stats.get_combined_metrics().accumulated_cost > 0
    finally:
        conv.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("api_mode", ["chat", "responses"])
@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(
    "status,budget_denied",
    [
        (code, False)
        for code in (408, 409, 429, 502, 520, 522, 524, 599, 402, 403, 405, 406, 410)
    ]
    + [(429, True)],
)
async def test_http_rate_limit_retry_and_budget_recovery(
    provider, api_mode, use_async, status, budget_denied
):
    base_url, replies, requests = provider
    replies.append(
        (
            status,
            {
                "error": {
                    "type": "budget_exceeded" if budget_denied else "provider_error",
                    "message": "Budget has been exceeded! Team=t"
                    if budget_denied
                    else "Provider request failed",
                    "code": str(status),
                }
            },
        )
    )
    if api_mode == "chat":
        success = {
            "id": "chat-1",
            "object": "chat.completion",
            "created": 1,
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "resumed"},
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    else:
        success = {
            "id": "resp-1",
            "object": "response",
            "created_at": 1,
            "model": "gpt-4o",
            "status": "completed",
            "parallel_tool_calls": False,
            "tool_choice": "auto",
            "tools": [],
            "output": [
                {
                    "type": "message",
                    "id": "msg-1",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": "resumed", "annotations": []}
                    ],
                }
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }
    replies.append((200, success))
    llm = LLM(
        model="openai/gpt-4o",
        base_url=base_url,
        api_key=SecretStr("test-key"),
        usage_id="budget-http",
        api_mode=api_mode,
        num_retries=3,
        retry_min_wait=0,
        retry_max_wait=0,
        retry_multiplier=0,
    )
    messages = [
        Message(
            role="system", content=[TextContent(text="You are a helpful assistant.")]
        ),
        Message(role="user", content=[TextContent(text="continue")]),
    ]

    async def call():
        return await llm.agenerate(messages) if use_async else llm.generate(messages)

    if budget_denied:
        with pytest.raises(LLMRateLimitError, match="Budget has been exceeded"):
            await call()
        assert len(requests) == 1

    if status in (402, 403, 405, 406, 410):
        with pytest.raises(Exception, match="Provider request failed"):
            await call()
        assert len(requests) == 1
        return

    response = await call()
    assert response.message.content == [TextContent(text="resumed")]
    assert len(requests) == 2


@pytest.mark.parametrize("api_mode", ["chat", "responses"])
@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
async def test_budgeted_request_keeps_output_cap_at_http_boundary(
    provider, api_mode, use_async
):
    base_url, replies, requests = provider
    response = {
        "id": "bounded-response",
        "model": "gpt-4o",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    if api_mode == "chat":
        response.update(
            object="chat.completion",
            created=1,
            choices=[
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "ok"},
                }
            ],
        )
    else:
        response.update(
            object="response",
            created_at=1,
            status="completed",
            parallel_tool_calls=False,
            tool_choice="auto",
            tools=[],
            output=[
                {
                    "type": "message",
                    "id": "bounded-message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": "ok", "annotations": []}
                    ],
                }
            ],
            usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        )
    replies.append((200, response))
    llm = LLM(
        model="openai/gpt-4o",
        base_url=base_url,
        api_key=SecretStr("fixture-key"),
        api_mode=api_mode,
        max_output_tokens=100,
        input_cost_per_token=0.000001,
        output_cost_per_token=0.000001,
        num_retries=0,
    )
    budget = CostBudget(1)
    context = LLMCallContext(cost_budget=budget)
    messages = [
        Message(role="system", content=[TextContent(text="Reply briefly.")]),
        Message(role="user", content=[TextContent(text="hi")]),
    ]

    result = (
        await llm.agenerate(messages, call_context=context, num_retries=9)
        if use_async
        else llm.generate(messages, call_context=context, num_retries=9)
    )

    assert result.message.content == [TextContent(text="ok")]
    assert len(requests) == 1
    cap = "max_completion_tokens" if api_mode == "chat" else "max_output_tokens"
    assert requests[0][cap] == 100
    assert budget.spent == pytest.approx(0.000002)


@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
async def test_budget_owns_retries_at_http_boundary(provider, use_async):
    base_url, replies, requests = provider
    replies.extend(
        [
            (503, {"error": {"type": "server_error", "message": "Fixture failure"}}),
            (200, create_mock_litellm_response("unguarded retry").model_dump()),
        ]
    )
    llm = LLM(
        model="openai/gpt-4o",
        base_url=base_url,
        api_key=SecretStr("fixture-key"),
        max_output_tokens=100,
        input_cost_per_token=0.0000005,
        output_cost_per_token=0.001,
        num_retries=2,
        retry_min_wait=0,
        retry_max_wait=0,
    )
    budget = CostBudget(0.2)
    context = LLMCallContext(cost_budget=budget)
    messages = [
        Message(role="system", content=[TextContent(text="Reply briefly.")]),
        Message(role="user", content=[TextContent(text="hi")]),
    ]

    with pytest.raises(LLMBudgetExceededError):
        if use_async:
            await llm.acompletion(messages, call_context=context, num_retries=2)
        else:
            llm.completion(messages, call_context=context, num_retries=2)

    assert len(requests) == 1
    assert budget.reserved == 0
    assert 0 < budget.spent <= budget.limit


@pytest.mark.asyncio
@pytest.mark.parametrize("api_mode", ["chat", "responses"])
@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(
    "code_field,error_code,exhausted",
    [
        ("code", "insufficient_quota", True),
        ("type", "insufficient_quota", True),
        ("type", "usage_limit_reached", True),
        ("type", "budget_exceeded", True),
        ("code", "rate_limit_exceeded", False),
    ],
)
async def test_http_structured_quota_skips_retries(
    provider, api_mode, use_async, code_field, error_code, exhausted
):
    base_url, replies, requests = provider
    error = {
        "message": "You exceeded your current quota, please check your plan.",
        "type": "provider_error",
        "code": "429",
    }
    error[code_field] = error_code
    replies.extend([(429, {"error": error})] * 3)
    llm = LLM(
        model="openai/gpt-4o",
        base_url=base_url,
        api_key=SecretStr("test-key"),
        api_mode=api_mode,
        num_retries=3,
        retry_min_wait=0,
        retry_max_wait=0,
        retry_multiplier=0,
    )
    messages = [
        Message(role="system", content=[TextContent(text="Be concise.")]),
        Message(role="user", content=[TextContent(text="ping")]),
    ]

    with pytest.raises(LLMRateLimitError) as caught:
        if use_async:
            await llm.agenerate(messages)
        else:
            llm.generate(messages)

    assert len(requests) == (1 if exhausted else llm.num_retries)
    # Profile pre-flight classifies the SDK exception after transport mapping.
    assert is_quota_exhaustion_error(caught.value) is exhausted


@pytest.mark.parametrize(
    "error_code,fallback_rate_limited",
    [
        ("insufficient_quota", False),
        ("insufficient_quota", True),
        ("budget_exceeded", False),
    ],
)
def test_http_structured_quota_fallback(
    provider, tmp_path, monkeypatch, error_code, fallback_rate_limited
):
    base_url, replies, requests = provider
    # Profiles omit API keys without a cipher; the fallback resolves this fake key.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    replies.append(
        (429, {"error": {"message": "Allowance exhausted", "type": error_code}})
    )
    if fallback_rate_limited:
        replies.append(
            (429, {"error": {"message": "Try again", "type": "rate_limit_exceeded"}})
        )
    replies.extend(
        [
            (
                200,
                {
                    "id": "chat-fallback",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "fallback"},
                        }
                    ],
                },
            ),
        ]
    )
    LLMProfileStore(tmp_path).save(
        "backup",
        LLM(
            model="openai/gpt-4o-mini",
            api_key=SecretStr("test-key"),
            base_url=base_url,
            num_retries=3,
            retry_min_wait=0,
            retry_max_wait=0,
            retry_multiplier=0,
        ),
    )
    llm = LLM(
        model="openai/gpt-4o",
        api_key=SecretStr("test-key"),
        base_url=base_url,
        num_retries=3,
        retry_min_wait=0,
        retry_max_wait=0,
        retry_multiplier=0,
        fallback_strategy=FallbackStrategy(
            fallback_llms=["backup"], profile_store_dir=tmp_path
        ),
    )
    messages = [
        Message(role="system", content=[TextContent(text="Be concise.")]),
        Message(role="user", content=[TextContent(text="ping")]),
    ]
    if error_code == "budget_exceeded":
        with pytest.raises(LLMRateLimitError):
            llm.generate(messages)
        assert [request["model"] for request in requests] == ["gpt-4o"]
    else:
        assert llm.generate(messages).message.content == [TextContent(text="fallback")]
        assert [request["model"] for request in requests] == ["gpt-4o"] + [
            "gpt-4o-mini"
        ] * (2 if fallback_rate_limited else 1)
