from types import SimpleNamespace
from unittest.mock import patch

import pytest
from litellm.responses.main import mock_responses_api_response
from litellm.types.llms.openai import (
    ResponseAPIUsage,
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

from openhands.sdk.llm import LLM, LLMStreamChunk
from openhands.sdk.llm.message import Message, ReasoningItemModel, TextContent
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
    # Temperature forced to 1.0 for Responses path
    assert out["temperature"] == 1.0
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


@patch("openhands.sdk.llm.llm.litellm_responses")
def test_llm_responses_streaming_invokes_token_callback(mock_responses_call):
    llm = LLM(model="gpt-5-mini")
    sys = Message(role="system", content=[TextContent(text="inst")])
    user = Message(role="user", content=[TextContent(text="hi")])

    final_resp = mock_responses_api_response("Streaming hello")

    delta_event = SimpleNamespace(
        type=ResponsesAPIStreamEvents.OUTPUT_TEXT_DELTA,
        delta="Streaming ",
        output_index=0,
        content_index=0,
        item_id="item-1",
    )
    completion_event = SimpleNamespace(
        type=ResponsesAPIStreamEvents.RESPONSE_COMPLETED,
        response=final_resp,
    )

    class DummyStream:
        def __init__(self, events):
            self._events: list[LLMStreamChunk] = events
            self._index: int = 0
            self.finished: bool = False
            self.completed_response: LLMStreamChunk | None = None

        def __iter__(self):
            return self

        def __next__(self):
            if self._index >= len(self._events):
                self.finished = True
                raise StopIteration
            event = self._events[self._index]
            self._index += 1
            if (
                getattr(event, "type", None)
                == ResponsesAPIStreamEvents.RESPONSE_COMPLETED
            ):
                self.completed_response = event
            return event

    stream = DummyStream([delta_event, completion_event])
    mock_responses_call.return_value = stream

    captured = []

    def on_token(event):
        captured.append(event)

    result = llm.responses([sys, user], on_token=on_token)

    assert [evt.type for evt in captured] == [
        ResponsesAPIStreamEvents.OUTPUT_TEXT_DELTA.value,
        ResponsesAPIStreamEvents.RESPONSE_COMPLETED.value,
    ]
    assert captured[0].text_delta == "Streaming "
    assert captured[1].is_final is True
    assert result.message.role == "assistant"
    assert "Streaming hello" in "".join(
        c.text for c in result.message.content if isinstance(c, TextContent)
    )
    assert stream.finished is True
    assert len(llm._telemetry.metrics.token_usages) == 1  # type: ignore[attr-defined]


def test_llm_responses_stream_requires_callback():
    llm = LLM(model="gpt-5-mini")
    sys = Message(role="system", content=[TextContent(text="inst")])
    user = Message(role="user", content=[TextContent(text="hi")])

    with pytest.raises(ValueError, match="requires an on_token callback"):
        llm.responses([sys, user], stream=True)
