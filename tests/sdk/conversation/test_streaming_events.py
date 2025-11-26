from __future__ import annotations

from litellm.responses.main import mock_responses_api_response
from rich.console import Console

from openhands.sdk.conversation.streaming_visualizer import (
    StreamingConversationVisualizer,
)
from openhands.sdk.event import StreamingDeltaEvent
from openhands.sdk.llm import LLM, LLMResponse, LLMStreamChunk
from openhands.sdk.llm.message import Message, TextContent
from openhands.sdk.llm.utils.metrics import MetricsSnapshot


class FakeStreamingLLM(LLM):
    def __init__(self) -> None:
        super().__init__(model="test-stream", usage_id="test-stream")
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


def test_visualizer_streaming_renders_incremental_text():
    viz = StreamingConversationVisualizer()
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
