"""Utility LLM calls must be distinguishable from main-loop turns in a trace."""

import json
import os
import subprocess
import sys
from typing import Any
from unittest.mock import patch

import pytest

from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.impl.remote_conversation import RemoteConversation
from openhands.sdk.observability.laminar import OPERATION_METADATA_KEY


METADATA_ATTRIBUTE_PREFIX = "lmnr.association.properties.metadata."


def _record_observe_kwargs(unbound_method: Any) -> dict[str, Any]:
    """Trigger the lazy ``observe`` build on a method and return the observe kwargs."""
    recorded: dict[str, Any] = {}

    def recorder(**kwargs: Any):
        recorded.update(kwargs)
        # Identity: leaves the decorated function's cached wrapper equivalent to
        # the undecorated function for the rest of the process.
        return lambda func: func

    with (
        patch("lmnr.observe", recorder),
        patch(
            "openhands.sdk.observability.laminar.should_enable_observability",
            return_value=True,
        ),
    ):
        try:
            unbound_method(object())
        except Exception:
            pass

    return recorded


@pytest.mark.parametrize(
    ("unbound_method", "expected_name", "expected_operation"),
    [
        (
            LocalConversation.generate_title,
            "conversation.generate_title",
            "title_generation",
        ),
        (
            RemoteConversation.generate_title,
            "conversation.generate_title",
            "title_generation",
        ),
        (LocalConversation.ask_agent, "conversation.ask_agent", "ask_agent"),
    ],
)
def test_utility_methods_declare_operation_metadata(
    unbound_method: Any, expected_name: str, expected_operation: str
) -> None:
    kwargs = _record_observe_kwargs(unbound_method)

    assert kwargs["name"] == expected_name
    assert kwargs["metadata"] == {OPERATION_METADATA_KEY: expected_operation}


def _probe_exported_spans() -> list[dict[str, Any]]:
    """Drive a real Laminar tracer and return the exported spans as plain dicts."""
    os.environ["LMNR_PROJECT_API_KEY"] = "test-key"

    import litellm
    from lmnr import Instruments, Laminar
    from lmnr.opentelemetry_lib.tracing import TracerWrapper
    from lmnr.opentelemetry_lib.tracing.processor import LaminarSpanProcessor
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    from pydantic import SecretStr

    from openhands.sdk.agent import Agent
    from openhands.sdk.conversation import Conversation
    from openhands.sdk.llm import LLM, Message, TextContent

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
    conversation = Conversation(
        agent=Agent(llm=llm, tools=[]),
        callbacks=[],
        observability_metadata={"repo": "OpenHands/software-agent-sdk"},
    )

    with patch(
        "openhands.sdk.llm.llm.litellm_completion", side_effect=mocked_completion
    ):
        conversation.send_message(
            Message(role="user", content=[TextContent(text="fix the auth bug")])
        )
        conversation.run()
        conversation.generate_title()
        conversation.ask_agent("what did you do?")

    Laminar.flush()

    spans = exporter.get_finished_spans()
    names_by_id = {
        span.context.span_id: span.name for span in spans if span.context is not None
    }
    return [
        {
            "name": span.name,
            "parent": names_by_id.get(span.parent.span_id) if span.parent else None,
            "attributes": {
                key: value
                for key, value in (span.attributes or {}).items()
                if key.startswith(METADATA_ATTRIBUTE_PREFIX)
            },
        }
        for span in spans
    ]


def _metadata(span: dict[str, Any]) -> dict[str, Any]:
    return {
        key.removeprefix(METADATA_ATTRIBUTE_PREFIX): value
        for key, value in span["attributes"].items()
    }


def test_operation_metadata_reaches_the_exported_llm_span() -> None:
    # Subprocess: Laminar.initialize() flips process-global tracing on for good,
    # which would change every later test in this worker.
    result = subprocess.run(
        [sys.executable, __file__],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    spans = json.loads(result.stdout.splitlines()[-1])

    llm_spans = [span for span in spans if span["name"] == "litellm.completion"]
    by_parent = {span["parent"]: span for span in llm_spans}
    assert set(by_parent) == {
        "agent.step",
        "conversation.generate_title",
        "conversation.ask_agent",
    }

    title_llm = by_parent["conversation.generate_title"]
    ask_llm = by_parent["conversation.ask_agent"]
    main_loop_llm = by_parent["agent.step"]

    # Spelled out, not derived from OPERATION_METADATA_KEY: this exact attribute
    # name is the wire contract downstream consumers hard-code.
    assert (
        title_llm["attributes"][
            "lmnr.association.properties.metadata.openhands.operation"
        ]
        == "title_generation"
    )
    assert (
        ask_llm["attributes"][
            "lmnr.association.properties.metadata.openhands.operation"
        ]
        == "ask_agent"
    )

    # Subtree-scoped: the main agent loop is untouched, and the conversation's
    # own trace metadata still reaches every span.
    assert OPERATION_METADATA_KEY not in _metadata(main_loop_llm)
    for span in llm_spans:
        assert _metadata(span)["repo"] == "OpenHands/software-agent-sdk"


if __name__ == "__main__":
    print(json.dumps(_probe_exported_spans()))
