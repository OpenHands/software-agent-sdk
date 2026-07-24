from unittest.mock import AsyncMock, patch

import pytest
from litellm.types.llms.openai import (
    OutputTextDeltaEvent,
    ResponseAPIUsage,
    ResponseCompletedEvent,
    ResponsesAPIResponse,
    ResponsesAPIStreamEvents,
)
from openai.types.responses.response_function_tool_call import ResponseFunctionToolCall
from openai.types.responses.response_output_message import ResponseOutputMessage
from openai.types.responses.response_output_text import ResponseOutputText
from openai.types.responses.response_reasoning_item import (
    ResponseReasoningItem,
    Summary,
)
from pydantic import SecretStr

from openhands.sdk.llm import LLM
from openhands.sdk.llm.llm import LLMCallContext
from openhands.sdk.llm.message import Message, ReasoningItemModel, TextContent
from openhands.sdk.llm.options.chat_options import select_chat_options
from openhands.sdk.llm.options.responses_options import select_responses_options


def build_responses_message_output(texts: list[str]) -> ResponseOutputMessage:
    parts = [
        ResponseOutputText(type="output_text", text=t, annotations=[]) for t in texts
    ]
    # Bypass stricter static type expectations in test context; runtime is fine
    return ResponseOutputMessage.model_construct(
        id="m1",
        type="message",
        role="assistant",
        status="completed",
        content=parts,  # type: ignore[arg-type]
    )


def test_from_llm_responses_output_parsing():
    # Build typed Responses output: assistant message text + function call + reasoning
    msg = build_responses_message_output(["Hello", "World"])  # concatenated
    fc = ResponseFunctionToolCall(
        type="function_call", name="do", arguments="{}", call_id="fc_1", id="fc_1"
    )
    reasoning = ResponseReasoningItem(
        id="rid",
        type="reasoning",
        summary=[
            Summary(type="summary_text", text="sum1"),
            Summary(type="summary_text", text="sum2"),
        ],
        content=None,
        encrypted_content=None,
        status="completed",
    )

    m = Message.from_llm_responses_output([msg, fc, reasoning])
    # Assistant text joined
    assert m.role == "assistant"
    assert [c.text for c in m.content if isinstance(c, TextContent)] == ["Hello\nWorld"]
    # Tool call normalized
    assert m.tool_calls and m.tool_calls[0].name == "do"
    # Reasoning mapped
    assert isinstance(m.responses_reasoning_item, ReasoningItemModel)
    assert m.responses_reasoning_item.summary == ["sum1", "sum2"]


def test_normalize_responses_kwargs_policy():
    llm = LLM(model="gpt-5-mini", reasoning_effort="high")
    # Use a model that is explicitly Responses-capable per model_features

    # enable encrypted reasoning and set max_output_tokens to test passthrough
    llm.enable_encrypted_reasoning = True
    llm.max_output_tokens = 128

    out = select_responses_options(
        llm, {"temperature": 0.3}, include=["text.output_text"], store=None
    )
    # Explicit sampling values are preserved when metadata does not reject them.
    assert out["temperature"] == 0.3
    assert out["tool_choice"] == "auto"
    # include should contain original and encrypted_content
    assert set(out["include"]) >= {"text.output_text", "reasoning.encrypted_content"}
    # store default to False when None passed
    assert out["store"] is False
    # reasoning config with effort only (no summary for unverified orgs)
    r = out["reasoning"]
    assert r["effort"] in {"low", "medium", "high", "none"}
    assert "summary" not in r  # Summary not included to support unverified orgs
    # max_output_tokens preserved
    assert out["max_output_tokens"] == 128


def test_responses_options_strip_sampling_when_metadata_rejects_it():
    llm = LLM(
        model="proxy/future-responses-model",
        api_mode="responses",
        temperature=0.7,
        capability_overrides={"supports_sampling_params": False},
    )

    out = select_responses_options(llm, {"temperature": 0.3}, include=None, store=None)

    assert "temperature" not in out


def test_normalize_responses_kwargs_with_summary():
    """Test reasoning_summary is included when set (verified orgs)."""
    llm = LLM(model="gpt-5-mini", reasoning_effort="high", reasoning_summary="detailed")

    out = select_responses_options(
        llm, {"temperature": 0.3}, include=["text.output_text"], store=None
    )
    # Verify reasoning includes both effort and summary when summary is set
    r = out["reasoning"]
    assert r["effort"] == "high"
    assert r["summary"] == "detailed"


def test_normalize_responses_kwargs_encrypted_reasoning_disabled():
    """Test that encrypted reasoning is NOT included when
    enable_encrypted_reasoning=False.
    """
    llm = LLM(model="gpt-4.1", reasoning_effort="medium")
    # Explicitly disable encrypted reasoning (also the default)
    llm.enable_encrypted_reasoning = False

    out = select_responses_options(llm, {}, include=["text.output_text"], store=None)
    # encrypted_content should NOT be in the include list
    assert "reasoning.encrypted_content" not in out.get("include", [])
    # But the original include item should still be there
    assert "text.output_text" in out["include"]


def test_responses_reasoning_options_not_sent_for_non_reasoning_model():
    llm = LLM(
        model="openai/gpt-4o-mini",
        reasoning_effort="high",
        reasoning_summary="detailed",
    )

    out = select_responses_options(
        llm,
        {},
        include=["message.output_text.logprobs"],
        store=None,
    )

    assert "reasoning" not in out
    assert out["include"] == ["message.output_text.logprobs"]


def test_responses_encrypted_reasoning_not_added_for_non_reasoning_model():
    llm = LLM(model="openai/gpt-4o-mini")

    out = select_responses_options(llm, {}, include=None, store=False)

    assert "include" not in out
    assert "reasoning" not in out


@patch("openhands.sdk.llm.llm.litellm_responses")
def test_llm_responses_end_to_end(mock_responses_call):
    # Configure LLM
    llm = LLM(model="gpt-5-mini")
    # messages: system + user
    sys = Message(role="system", content=[TextContent(text="inst")])
    user = Message(role="user", content=[TextContent(text="hi")])

    # Build typed ResponsesAPIResponse with usage
    msg = build_responses_message_output(["ok"])
    usage = ResponseAPIUsage(input_tokens=10, output_tokens=5, total_tokens=15)
    resp = ResponsesAPIResponse(
        id="r1",
        created_at=0,
        output=[msg],
        parallel_tool_calls=False,
        tool_choice="auto",
        top_p=None,
        tools=[],
        usage=usage,
        instructions="inst",
        status="completed",
    )

    mock_responses_call.return_value = resp

    result = llm.responses([sys, user])
    # Returned message is assistant with text
    assert result.message.role == "assistant"
    assert [c.text for c in result.message.content if isinstance(c, TextContent)] == [
        "ok"
    ]
    # Telemetry should have recorded usage (one entry)
    assert len(llm._telemetry.metrics.token_usages) == 1  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "model",
    [
        "gpt-5.1-codex-mini",
        "openai/gpt-5.1-codex-mini",
    ],
)
def test_responses_reasoning_effort_none_not_sent_for_gpt_5_1(model):
    llm = LLM(model=model, reasoning_effort=None)
    out = select_responses_options(llm, {}, include=None, store=None)
    # When reasoning_effort is None, there should be no 'reasoning' key
    assert "reasoning" not in out


def test_chat_and_responses_options_prompt_cache_retention_gpt_5_plus_and_non_gpt():
    # Confirm allowed: 5.1 codex mini supports extended retention per docs
    llm_51_codex_mini = LLM(model="openai/gpt-5.1-codex-mini")
    opts_51_codex_mini_resp = select_responses_options(
        llm_51_codex_mini, {}, include=None, store=None
    )
    assert opts_51_codex_mini_resp.get("prompt_cache_retention") == "24h"

    # New GPT-5.2 variants should include prompt_cache_retention
    llm_52 = LLM(model="openai/gpt-5.2")
    assert (
        select_chat_options(llm_52, {}, has_tools=False).get("prompt_cache_retention")
        == "24h"
    )
    assert (
        select_responses_options(llm_52, {}, include=None, store=None).get(
            "prompt_cache_retention"
        )
        == "24h"
    )

    llm_52_chat_latest = LLM(model="openai/gpt-5.2-chat-latest")
    assert (
        select_chat_options(llm_52_chat_latest, {}, has_tools=False).get(
            "prompt_cache_retention"
        )
        == "24h"
    )

    # GPT-5.1 (non-mini) should include prompt_cache_retention; mini variants should not
    llm_51_mini = LLM(model="openai/gpt-5.1-mini")
    opts_51_mini_chat = select_chat_options(llm_51_mini, {}, has_tools=False)
    assert "prompt_cache_retention" not in opts_51_mini_chat

    opts_51_mini_resp = select_responses_options(
        llm_51_mini, {}, include=None, store=None
    )
    assert "prompt_cache_retention" not in opts_51_mini_resp

    llm_5_mini = LLM(model="openai/gpt-5-mini")
    opts_5_mini_chat = select_chat_options(llm_5_mini, {}, has_tools=False)
    assert "prompt_cache_retention" not in opts_5_mini_chat

    opts_5_mini_resp = select_responses_options(
        llm_5_mini, {}, include=None, store=None
    )
    assert "prompt_cache_retention" not in opts_5_mini_resp

    llm_41 = LLM(model="openai/gpt-4.1")
    opts_41_chat = select_chat_options(llm_41, {}, has_tools=False)
    assert opts_41_chat.get("prompt_cache_retention") == "24h"

    opts_41_resp = select_responses_options(llm_41, {}, include=None, store=None)
    assert opts_41_resp.get("prompt_cache_retention") == "24h"

    llm_41_azure = LLM(model="azure/gpt-4.1")
    opts_41_azure_chat = select_chat_options(llm_41_azure, {}, has_tools=False)
    assert "prompt_cache_retention" not in opts_41_azure_chat

    opts_41_azure_resp = select_responses_options(
        llm_41_azure, {}, include=None, store=None
    )
    assert "prompt_cache_retention" not in opts_41_azure_resp

    llm_51_azure = LLM(model="azure/gpt-5.1")
    opts_51_azure_chat = select_chat_options(llm_51_azure, {}, has_tools=False)
    assert "prompt_cache_retention" not in opts_51_azure_chat

    opts_51_azure_resp = select_responses_options(
        llm_51_azure, {}, include=None, store=None
    )
    assert "prompt_cache_retention" not in opts_51_azure_resp

    # Other non-GPT-5 models should not include it at all
    llm_other = LLM(model="gpt-4o")
    opts_other_chat = select_chat_options(llm_other, {}, has_tools=False)
    assert "prompt_cache_retention" not in opts_other_chat

    opts_other_resp = select_responses_options(llm_other, {}, include=None, store=None)
    assert "prompt_cache_retention" not in opts_other_resp


def test_responses_options_forwards_prompt_cache_key_when_set():
    """Regression test for #2904."""
    llm = LLM(model="openai/gpt-5.1")
    llm._call_context = LLMCallContext(prompt_cache_key="conv-abc123")
    assert (
        select_responses_options(llm, {}, include=None, store=None).get(
            "prompt_cache_key"
        )
        == "conv-abc123"
    )


def test_responses_options_omits_prompt_cache_key_when_unset():
    llm = LLM(model="openai/gpt-5.1")
    assert "prompt_cache_key" not in select_responses_options(
        llm, {}, include=None, store=None
    )


@patch("openhands.sdk.llm.llm.litellm_responses")
def test_responses_retries_without_caching_on_prompt_cache_too_small(mock_responses):
    """When Vertex AI rejects caching due to small content, responses() should
    retry without prompt caching while preserving caller kwargs.

    Mirrors test_completion_retries_without_caching_on_prompt_cache_too_small in
    test_llm_completion.py, but exercises the Responses API path. The two
    methods differ in signature (``include``, ``store`` positional args) and in
    how ``stream`` is resolved, so they need independent coverage.
    """
    from litellm.exceptions import BadRequestError

    cache_error = BadRequestError(
        (
            "Vertex_aiException BadRequestError - "
            '{"error":{"code":400,'
            '"message":"The cached content is of 1171 tokens. '
            'The minimum token count to start caching is 4096.",'
            '"status":"INVALID_ARGUMENT"}}'
        ),
        model="gemini-3-flash",
        llm_provider="vertex_ai",
    )

    # Build a typed ResponsesAPIResponse for the successful retry
    msg = build_responses_message_output(["Retry succeeded"])
    usage = ResponseAPIUsage(input_tokens=0, output_tokens=0, total_tokens=0)
    success_resp = ResponsesAPIResponse(
        id="r1",
        created_at=0,
        output=[msg],
        parallel_tool_calls=False,
        tool_choice="auto",
        top_p=None,
        tools=[],
        usage=usage,
        instructions="",
        status="completed",
    )
    mock_responses.side_effect = [cache_error, success_resp]

    # Pick a model that supports prompt caching so is_caching_prompt_active()
    # is True and the retry branch is reachable on the responses() path.
    # (Gemini no longer uses explicit caching, so use an Anthropic model here.)
    llm = LLM(
        model="claude-sonnet-4-20250514",
        api_key=SecretStr("test_key"),
        usage_id="test-llm",
        caching_prompt=True,
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
    )

    messages = [
        Message(role="system", content=[TextContent(text="sys")]),
        Message(role="user", content=[TextContent(text="Hello")]),
    ]

    # Pass caller kwargs that must survive the retry. ``metadata`` flows through
    # ``**kwargs`` (not a named param), so it's the cleanest probe for the
    # ``_caller_kwargs`` forwarding fix; ``store`` exercises the positional-arg
    # path on the retry call signature.
    response = llm.responses(
        messages,
        store=False,
        metadata={"trace_id": "abc-123"},
    )

    # Two calls: first with caching active (fails), second without (succeeds).
    assert mock_responses.call_count == 2
    assert response.raw_response is success_resp

    # Caller kwargs preserved on the retry — without ``_caller_kwargs`` the
    # retry would silently drop them.
    second_kwargs = mock_responses.call_args_list[1].kwargs
    assert second_kwargs.get("store") is False
    assert second_kwargs.get("metadata") == {"trace_id": "abc-123"}


@pytest.mark.asyncio
@patch("openhands.sdk.llm.llm.litellm_aresponses", new_callable=AsyncMock)
async def test_aresponses_retries_without_caching_on_prompt_cache_too_small(
    mock_aresponses,
):
    """Async version of the sync responses prompt-cache-too-small retry test.

    Ensures aresponses() also retries without prompt caching when Vertex AI
    rejects the request due to cache content below the minimum token
    threshold, and preserves caller kwargs (positional ``store`` and
    ``**kwargs`` metadata). Mirrors
    test_responses_retries_without_caching_on_prompt_cache_too_small.
    """
    from litellm.exceptions import BadRequestError

    cache_error = BadRequestError(
        (
            "Vertex_aiException BadRequestError - "
            '{"error":{"code":400,'
            '"message":"The cached content is of 1171 tokens. '
            'The minimum token count to start caching is 4096.",'
            '"status":"INVALID_ARGUMENT"}}'
        ),
        model="gemini-3-flash",
        llm_provider="vertex_ai",
    )

    msg = build_responses_message_output(["Retry succeeded"])
    usage = ResponseAPIUsage(input_tokens=0, output_tokens=0, total_tokens=0)
    success_resp = ResponsesAPIResponse(
        id="r1",
        created_at=0,
        output=[msg],
        parallel_tool_calls=False,
        tool_choice="auto",
        top_p=None,
        tools=[],
        usage=usage,
        instructions="",
        status="completed",
    )
    mock_aresponses.side_effect = [cache_error, success_resp]

    # Anthropic model so is_caching_prompt_active() is True (Gemini no longer
    # uses explicit caching); mirrors the sync test above.
    llm = LLM(
        model="claude-sonnet-4-20250514",
        api_key=SecretStr("test_key"),
        usage_id="test-llm",
        caching_prompt=True,
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
    )

    messages = [
        Message(role="system", content=[TextContent(text="sys")]),
        Message(role="user", content=[TextContent(text="Hello")]),
    ]

    response = await llm.aresponses(
        messages,
        store=False,
        metadata={"trace_id": "abc-123"},
    )

    assert mock_aresponses.call_count == 2
    assert response.raw_response is success_resp

    second_kwargs = mock_aresponses.call_args_list[1].kwargs
    assert second_kwargs.get("store") is False
    assert second_kwargs.get("metadata") == {"trace_id": "abc-123"}


def _make_wrapped_response_stream_events(text: str = "Hello wrapped stream"):
    msg = build_responses_message_output([text])
    usage = ResponseAPIUsage(input_tokens=1, output_tokens=1, total_tokens=2)
    response = ResponsesAPIResponse(
        id="resp-wrapped-stream",
        created_at=0,
        output=[msg],
        parallel_tool_calls=False,
        tool_choice="auto",
        top_p=None,
        tools=[],
        usage=usage,
        instructions="",
        status="completed",
    )
    events = [
        OutputTextDeltaEvent(
            type=ResponsesAPIStreamEvents.OUTPUT_TEXT_DELTA,
            item_id="m1",
            output_index=0,
            content_index=0,
            delta=text,
        ),
        ResponseCompletedEvent(
            type=ResponsesAPIStreamEvents.RESPONSE_COMPLETED,
            response=response,
        ),
    ]
    return events, response


@patch("openhands.sdk.llm.llm.litellm_responses")
def test_responses_streaming_accepts_wrapped_iterable(mock_responses):
    """Responses streaming must not require LiteLLM's concrete iterator class."""
    events, completed_response = _make_wrapped_response_stream_events()
    mock_responses.return_value = iter(events)

    llm = LLM(
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        usage_id="test-llm",
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
    )

    received = []
    response = llm.responses(
        [Message(role="user", content=[TextContent(text="Hello")])],
        stream=True,
        on_token=received.append,
    )

    assert response.raw_response is completed_response
    assert [chunk.choices[0].delta.content for chunk in received] == [
        "Hello wrapped stream"
    ]


@pytest.mark.asyncio
@patch("openhands.sdk.llm.llm.litellm_aresponses", new_callable=AsyncMock)
async def test_aresponses_streaming_accepts_sync_generator(mock_aresponses):
    """Async Responses streaming must also tolerate sync iterable wrappers."""
    events, completed_response = _make_wrapped_response_stream_events()

    def _return_sync_generator(*args, **kwargs):
        return (event for event in events)

    mock_aresponses.side_effect = _return_sync_generator

    llm = LLM(
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        usage_id="test-llm",
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
    )

    received = []
    response = await llm.aresponses(
        [Message(role="user", content=[TextContent(text="Hello")])],
        stream=True,
        on_token=received.append,
    )

    assert response.raw_response is completed_response
    assert [chunk.choices[0].delta.content for chunk in received] == [
        "Hello wrapped stream"
    ]


@pytest.mark.asyncio
@patch("openhands.sdk.llm.llm.litellm_aresponses", new_callable=AsyncMock)
async def test_aresponses_streaming_accepts_async_generator(mock_aresponses):
    """Regression for lmnr 0.7.47 returning an async_generator wrapper."""
    events, completed_response = _make_wrapped_response_stream_events()

    async def _events():
        for event in events:
            yield event

    mock_aresponses.return_value = _events()

    llm = LLM(
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        usage_id="test-llm",
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
    )

    received = []
    response = await llm.aresponses(
        [Message(role="user", content=[TextContent(text="Hello")])],
        stream=True,
        on_token=received.append,
    )

    assert response.raw_response is completed_response
    assert [chunk.choices[0].delta.content for chunk in received] == [
        "Hello wrapped stream"
    ]
