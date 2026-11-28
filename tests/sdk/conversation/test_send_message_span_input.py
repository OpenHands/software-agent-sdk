"""Tracing must not export credential-like send-message input verbatim."""

import json
import threading
import uuid
from collections.abc import Iterator
from typing import cast
from unittest.mock import MagicMock

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from pydantic import SecretStr

from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation import Conversation, LocalConversation
from openhands.sdk.conversation.impl.remote_conversation import RemoteConversation
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.conversation.types import ConversationCallbackType
from openhands.sdk.event.llm_convertible import SystemPromptEvent
from openhands.sdk.llm import LLM, Message, TextContent


RAW_SECRET = "tok-span-123"
SECRET_MESSAGE = f'keep this readable {{"api_token": "{RAW_SECRET}"}}'


class _DummyAgent(AgentBase):
    def __init__(self):
        super().__init__(
            llm=LLM(
                model="gpt-4o-mini",
                api_key=SecretStr("test-key"),
                usage_id="test-llm",
            ),
            tools=[],
        )

    def init_state(
        self, state: ConversationState, on_event: ConversationCallbackType
    ) -> None:
        on_event(
            SystemPromptEvent(
                source="agent",
                system_prompt=TextContent(text="dummy"),
                tools=[],
            )
        )

    def step(self, conversation: LocalConversation, on_event, on_token=None) -> None:
        raise AssertionError("send_message must not run the agent")


@pytest.fixture
def span_exporter() -> Iterator[InMemorySpanExporter]:
    """Capture real Laminar spans without exporting them over the network."""
    from lmnr import Laminar
    from lmnr.opentelemetry_lib.opentelemetry.instrumentation.threading import (
        ThreadingInstrumentor,
    )
    from lmnr.opentelemetry_lib.tracing import TracerWrapper
    from lmnr.opentelemetry_lib.tracing.processor import LaminarSpanProcessor
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    from openhands.sdk.observability import laminar

    assert not TracerWrapper.verify_initialized()
    exporter = InMemorySpanExporter()
    original_thread_init = threading.Thread.__init__
    Laminar.initialize(
        project_api_key="test-key",
        disable_batch=True,
        instruments=set(),
        set_global_tracer_provider=False,
    )
    processor = TracerWrapper.instance._span_processor
    assert isinstance(processor, LaminarSpanProcessor)
    processor.instance = SimpleSpanProcessor(exporter)
    laminar._observability_enabled = False
    try:
        yield exporter
    finally:
        laminar._observability_enabled = False
        Laminar.shutdown()
        ThreadingInstrumentor().uninstrument()
        threading.Thread.__init__ = original_thread_init  # type: ignore[method-assign]
        TracerWrapper._original_thread_init = None
        if hasattr(TracerWrapper, "instance"):
            del TracerWrapper.instance


def _send_message_input(exporter: InMemorySpanExporter) -> dict:
    spans = [
        span
        for span in exporter.get_finished_spans()
        if span.name == "conversation.send_message"
    ]
    assert len(spans) == 1
    span_input = cast(str, (spans[0].attributes or {})["lmnr.span.input"])
    return json.loads(span_input)


@pytest.mark.parametrize("text", [SECRET_MESSAGE, "ordinary message"])
def test_local_send_message_span_redacts_only_credentials(span_exporter, text):
    conversation = Conversation(agent=_DummyAgent())
    try:
        conversation.send_message(text, sender="test-user")
    finally:
        conversation.close()

    recorded = _send_message_input(span_exporter)
    assert recorded["sender"] == "test-user"
    if text == SECRET_MESSAGE:
        assert RAW_SECRET not in recorded["message"]
        assert "<redacted>" in recorded["message"]
        assert "keep this readable" in recorded["message"]
    else:
        assert recorded["message"] == text


@pytest.mark.parametrize("text", [SECRET_MESSAGE, "ordinary message"])
def test_remote_send_message_span_redacts_only_credentials(span_exporter, text):
    conversation = RemoteConversation.__new__(RemoteConversation)
    conversation._client = MagicMock()
    conversation._id = uuid.uuid4()
    conversation._observability_root_span = None

    conversation.send_message(
        Message(role="user", content=[TextContent(text=text)]),
        sender="test-user",
    )

    recorded = _send_message_input(span_exporter)
    assert recorded["sender"] == "test-user"
    recorded_text = recorded["message"]["content"][0]["text"]
    if text == SECRET_MESSAGE:
        assert RAW_SECRET not in recorded_text
        assert "<redacted>" in recorded_text
        assert "keep this readable" in recorded_text
    else:
        assert recorded_text == text
