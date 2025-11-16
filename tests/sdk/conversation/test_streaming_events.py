from __future__ import annotations

from litellm.responses.main import mock_responses_api_response
from rich.console import Console

from openhands.sdk import Conversation
from openhands.sdk.agent import Agent
from openhands.sdk.event import MessageEvent, StreamingDeltaEvent
from openhands.sdk.llm import LLM, LLMResponse, LLMStreamChunk
from openhands.sdk.llm.message import Message, TextContent
from openhands.sdk.llm.utils.metrics import MetricsSnapshot


class FakeStreamingLLM(LLM):
    def __init__(self) -> None:
        super().__init__(model="test-stream", service_id="test-stream")
        self._stream_events: list[LLMStreamChunk] = [
            LLMStreamChunk(
                type="response.output_text.delta",
                part_kind="assistant_message",
                text_delta="Hello",
                output_index=0,
                content_index=0,
                item_id="item-1",
                response_id="resp-test",
            ),
            LLMStreamChunk(
                type="response.output_text.delta",
                part_kind="assistant_message",
                text_delta=" world",
                output_index=0,
                content_index=0,
                item_id="item-1",
                response_id="resp-test",
            ),
            LLMStreamChunk(
                type="response.output_text.done",
                part_kind="assistant_message",
                is_final=True,
                output_index=0,
                content_index=0,
                item_id="item-1",
                response_id="resp-test",
            ),
            LLMStreamChunk(
                type="response.completed",
                part_kind="status",
                is_final=True,
                output_index=0,
                content_index=0,
                item_id="item-1",
                response_id="resp-test",
            ),
        ]

    def uses_responses_api(self) -> bool:  # pragma: no cover - simple override
        return True

    def responses(
        self,
        messages,
        tools=None,
        include=None,
        store=None,
        _return_metrics=False,
        add_security_risk_prediction=False,
        on_token=None,
        **kwargs,
    ):
        if on_token:
            for event in self._stream_events:
                on_token(event)

        message = Message(
            role="assistant",
            content=[TextContent(text="Hello world")],
        )
        snapshot = MetricsSnapshot(
            model_name=self.metrics.model_name,
            accumulated_cost=self.metrics.accumulated_cost,
            max_budget_per_task=self.metrics.max_budget_per_task,
            accumulated_token_usage=self.metrics.accumulated_token_usage,
        )
        raw_response = mock_responses_api_response("Hello world")
        if self._telemetry:
            self._telemetry.on_response(raw_response)
        return LLMResponse(message=message, metrics=snapshot, raw_response=raw_response)


def test_streaming_events_persist_and_dispatch(tmp_path):
    llm = FakeStreamingLLM()
    agent = Agent(llm=llm, tools=[])

    tokens: list[LLMStreamChunk] = []
    callback_events = []

    def token_cb(event: LLMStreamChunk) -> None:
        tokens.append(event)

    def recorder(event) -> None:
        callback_events.append(event)

    conversation = Conversation(
        agent=agent,
        workspace=str(tmp_path),
        callbacks=[recorder],
        token_callbacks=[token_cb],
        visualize=False,
    )

    conversation.send_message("Say hello")
    conversation.run()

    stream_events = [
        event
        for event in conversation.state.events
        if isinstance(event, StreamingDeltaEvent)
    ]

    assert len(stream_events) == len(llm._stream_events)
    assert [evt.stream_chunk.type for evt in stream_events] == [
        evt.type for evt in llm._stream_events
    ]
    assert [evt.stream_chunk.part_kind for evt in stream_events[:3]] == [
        "assistant_message",
        "assistant_message",
        "assistant_message",
    ]
    assert stream_events[-2].stream_chunk.is_final is True
    assert stream_events[-2].stream_chunk.part_kind == "assistant_message"
    assert stream_events[-1].stream_chunk.part_kind == "status"

    assert [evt.type for evt in tokens] == [evt.type for evt in llm._stream_events]

    stream_indices = [
        idx
        for idx, event in enumerate(callback_events)
        if isinstance(event, StreamingDeltaEvent)
    ]
    final_message_index = next(
        idx
        for idx, event in enumerate(callback_events)
        if isinstance(event, MessageEvent) and event.source == "agent"
    )

    assert stream_indices  # streaming events received via callbacks
    assert all(idx < final_message_index for idx in stream_indices)


def test_visualizer_streaming_renders_incremental_text():
    from openhands.sdk.conversation.streaming_visualizer import (
        create_streaming_visualizer,
    )

    viz = create_streaming_visualizer()
    viz._console = Console(record=True)
    viz._use_live = viz._console.is_terminal

    reasoning_start = LLMStreamChunk(
        type="response.reasoning_summary_text.delta",
        part_kind="reasoning_summary",
        text_delta="Think",
        output_index=0,
        content_index=0,
        item_id="reasoning-1",
        response_id="resp-test",
    )
    reasoning_continue = LLMStreamChunk(
        type="response.reasoning_summary_text.delta",
        part_kind="reasoning_summary",
        text_delta=" deeply",
        output_index=0,
        content_index=0,
        item_id="reasoning-1",
        response_id="resp-test",
    )
    reasoning_end = LLMStreamChunk(
        type="response.reasoning_summary_text.delta",
        part_kind="reasoning_summary",
        is_final=True,
        output_index=0,
        content_index=0,
        item_id="reasoning-1",
        response_id="resp-test",
    )

    viz.on_event(StreamingDeltaEvent(source="agent", stream_chunk=reasoning_start))
    viz.on_event(StreamingDeltaEvent(source="agent", stream_chunk=reasoning_continue))
    viz.on_event(StreamingDeltaEvent(source="agent", stream_chunk=reasoning_end))

    output = viz._console.export_text()
    assert "Reasoning:" in output
    assert "Think deeply" in output
