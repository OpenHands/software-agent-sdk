"""Auto-titling is the only title path a deployed agent-server actually runs.

It calls the title helper from an executor thread, so its LLM span joins the
conversation trace only if the root span is explicitly re-attached there.
"""

import json
import os
import subprocess
import sys
from typing import Any


OPERATION_ATTRIBUTE = "lmnr.association.properties.metadata.openhands.operation"


def _probe_auto_title_spans() -> dict[str, Any]:
    """Drive AutoTitleSubscriber through a real Laminar tracer; return its spans."""
    os.environ["LMNR_PROJECT_API_KEY"] = "test-key"

    import asyncio
    from unittest.mock import AsyncMock, patch
    from uuid import uuid4

    import litellm
    from lmnr import Instruments, Laminar
    from lmnr.opentelemetry_lib.tracing import TracerWrapper
    from lmnr.opentelemetry_lib.tracing.processor import LaminarSpanProcessor
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    from pydantic import SecretStr

    from openhands.agent_server.conversation_service import AutoTitleSubscriber
    from openhands.agent_server.event_service import EventService
    from openhands.agent_server.models import StoredConversation
    from openhands.sdk.agent import Agent
    from openhands.sdk.conversation import Conversation
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation
    from openhands.sdk.event.llm_convertible import MessageEvent
    from openhands.sdk.llm import LLM, Message, TextContent
    from openhands.sdk.security.confirmation_policy import NeverConfirm
    from openhands.sdk.workspace import LocalWorkspace

    Laminar.initialize(
        project_api_key="test-key",
        base_url="http://localhost",
        http_port=1,
        grpc_port=1,
        disable_batch=True,
        instruments={Instruments.LITELLM},
    )
    exporter = InMemorySpanExporter()
    span_processor = TracerWrapper.instance._span_processor
    assert isinstance(span_processor, LaminarSpanProcessor)
    span_processor.instance = SimpleSpanProcessor(exporter)

    instrumented_completion = litellm.completion

    def mocked_completion(**kwargs: Any):
        return instrumented_completion(**{**kwargs, "mock_response": "Fix Auth Bug"})

    llm = LLM(usage_id="probe-llm", model="gpt-4o", api_key=SecretStr("test-key"))
    agent = Agent(llm=llm, tools=[])
    conversation = Conversation(
        agent=agent,
        callbacks=[],
        observability_metadata={"repo": "OpenHands/software-agent-sdk"},
    )
    assert isinstance(conversation, LocalConversation)

    stored = StoredConversation(
        id=uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir="workspace/project"),
        confirmation_policy=NeverConfirm(),
        initial_message=None,
        metrics=None,
        title=None,
    )
    service = AsyncMock(spec=EventService)
    service.stored = stored
    service._conversation = conversation

    event = MessageEvent(
        id="evt-1",
        source="user",
        llm_message=Message(
            role="user", content=[TextContent(text="fix the auth bug")]
        ),
    )

    async def drive() -> None:
        with patch(
            "openhands.sdk.llm.llm.litellm_completion", side_effect=mocked_completion
        ):
            await AutoTitleSubscriber(service=service)(event)
            for _ in range(250):
                await asyncio.sleep(0.02)
                if stored.title is not None:
                    return

    asyncio.run(drive())
    Laminar.flush()

    root_span = conversation._observability_root_span
    assert root_span is not None
    spans = exporter.get_finished_spans()
    names_by_id = {
        span.context.span_id: span.name for span in spans if span.context is not None
    }
    return {
        "title": stored.title,
        "conversation_trace_id": root_span.span.get_span_context().trace_id,
        "spans": [
            {
                "name": span.name,
                "parent": names_by_id.get(span.parent.span_id) if span.parent else None,
                "trace_id": span.context.trace_id if span.context else None,
                "attributes": dict(span.attributes or {}),
            }
            for span in spans
        ],
    }


def test_auto_title_llm_span_joins_the_conversation_trace() -> None:
    # Subprocess: Laminar.initialize() flips process-global tracing on for good,
    # which would change every later test in this worker.
    result = subprocess.run(
        [sys.executable, __file__],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    probe = json.loads(result.stdout.splitlines()[-1])

    assert probe["title"] == "Fix Auth Bug"

    llm_spans = [
        span for span in probe["spans"] if span["name"] == "litellm.completion"
    ]
    assert len(llm_spans) == 1
    title_llm = llm_spans[0]

    assert title_llm["parent"] == "conversation.generate_title"
    assert title_llm["trace_id"] == probe["conversation_trace_id"]

    # Spelled out, not derived from OPERATION_METADATA_KEY: this exact attribute
    # name is the wire contract downstream consumers hard-code.
    assert title_llm["attributes"][OPERATION_ATTRIBUTE] == "title_generation"


if __name__ == "__main__":
    print(json.dumps(_probe_auto_title_spans()))
