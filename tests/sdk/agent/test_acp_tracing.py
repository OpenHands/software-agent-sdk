"""ACP turns must emit LLM/TOOL spans shaped like the native agent's.

A trace consumer reconstructs a trajectory from ``span_type`` plus the LLM
span's ``output`` (a list of assistant messages) and each TOOL span's
``output``. These assert that contract rather than that spans merely exist.
"""

import json
from typing import Any
from unittest.mock import patch

import pytest

from openhands.sdk.agent.acp_tracing import (
    ACP_SERVER_METADATA_KEY,
    AGENT_KIND_METADATA_KEY,
    TURN_SPAN_NAME,
    ACPTurnTrace,
)


METADATA_PREFIX = "lmnr.association.properties.metadata."


@pytest.fixture
def exported():
    """Capture the spans this test emits, whatever the ambient lmnr state.

    Two paths, because these tests must never skip — a skipped tracing test is
    indistinguishable from a passing one, and ``LMNR_*`` env vars are set in real
    CI. When lmnr is already up (env vars, or an earlier test) its span processor
    is borrowed and restored; that also keeps test spans off whatever real
    endpoint it was configured with. Otherwise one is built here, with the
    in-memory exporter installed *before* ``initialize`` so no OTLP endpoint is
    created — an unreachable one leaves later tests retrying exports with backoff.
    """
    import threading

    from lmnr import Laminar
    from lmnr.opentelemetry_lib.opentelemetry.instrumentation.threading import (
        ThreadingInstrumentor,
    )
    from lmnr.opentelemetry_lib.tracing import TracerWrapper
    from lmnr.opentelemetry_lib.tracing.processor import LaminarSpanProcessor
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    borrowed = TracerWrapper.verify_initialized()
    original_thread_init = threading.Thread.__init__

    if not borrowed:
        TracerWrapper(
            exporter=exporter,
            disable_batch=True,
            instruments=set(),
            set_global_tracer_provider=False,
        )
    if not Laminar.is_initialized():
        # Respects an existing TracerWrapper rather than building a second one.
        Laminar.initialize(
            project_api_key="test-key",
            disable_batch=True,
            instruments=set(),
            set_global_tracer_provider=False,
        )

    processor = TracerWrapper.instance._span_processor
    assert isinstance(processor, LaminarSpanProcessor)
    previous = processor.instance
    processor.instance = SimpleSpanProcessor(exporter)
    try:
        yield exporter.get_finished_spans
    finally:
        processor.instance = previous
        if not borrowed:
            Laminar.shutdown()
            ThreadingInstrumentor().uninstrument()
            threading.Thread.__init__ = original_thread_init  # type: ignore[method-assign]
            TracerWrapper._original_thread_init = None
            if hasattr(TracerWrapper, "instance"):
                del TracerWrapper.instance


def _tool_entry(call_id: str, **over: Any) -> dict[str, Any]:
    entry = {
        "tool_call_id": call_id,
        "title": f"Read {call_id}.py",
        "tool_kind": "read",
        "status": "completed",
        "raw_input": {"path": f"{call_id}.py"},
        "raw_output": f"contents of {call_id}",
        "content": None,
    }
    entry.update(over)
    return entry


def _by_type(spans, span_type: str):
    return [s for s in spans if (s.attributes or {}).get("lmnr.span.type") == span_type]


def _meta(span, key: str):
    return (span.attributes or {}).get(METADATA_PREFIX + key)


def _tool_text(span) -> str:
    """Pull the result text out the way a consumer's content-flattener does."""
    payload = json.loads((span.attributes or {})["lmnr.span.output"])
    return payload["content"][0]["text"]


def test_turn_emits_an_llm_span_whose_output_is_an_assistant_message(exported):
    trace = ACPTurnTrace(acp_server="claude-code", model_id="claude-sonnet-4-5")
    trace.start_turn("read the file")
    entry = _tool_entry("call_1")
    trace.tool_started(entry)
    trace.tool_finished(entry)
    trace.finish_turn("Read it.", "thinking...", [entry])

    llm = _by_type(exported(), "LLM")
    assert len(llm) == 1
    assert llm[0].name == TURN_SPAN_NAME

    output = json.loads((llm[0].attributes or {})["lmnr.span.output"])
    assert isinstance(output, list) and len(output) == 1
    message = output[0]
    assert message["role"] == "assistant"
    assert message["content"] == "Read it."
    assert message["reasoning_content"] == "thinking..."

    # The exporter reads id + function.name/arguments off each tool call.
    (call,) = message["tool_calls"]
    assert call["id"] == "call_1"
    assert call["function"]["name"] == "read"
    assert json.loads(call["function"]["arguments"]) == {"path": "call_1.py"}


def test_tool_span_carries_output_and_correlating_call_id(exported):
    trace = ACPTurnTrace(acp_server="codex", model_id=None)
    trace.start_turn("go")
    entry = _tool_entry("call_9")
    trace.tool_started(entry)
    trace.tool_finished(entry)
    trace.finish_turn("done", "", [entry])

    (tool,) = _by_type(exported(), "TOOL")
    assert tool.name == "Read call_9.py"
    assert _tool_text(tool) == "contents of call_9"
    assert _meta(tool, "tool_call_id") == "call_9"


def test_tool_spans_are_children_of_the_turn_span(exported):
    trace = ACPTurnTrace(acp_server="codex", model_id=None)
    trace.start_turn("go")
    entry = _tool_entry("call_1")
    trace.tool_started(entry)
    trace.tool_finished(entry)
    trace.finish_turn("done", "", [entry])

    spans = exported()
    (llm,) = _by_type(spans, "LLM")
    (tool,) = _by_type(spans, "TOOL")
    assert tool.parent is not None
    assert tool.parent.span_id == llm.context.span_id
    assert tool.context.trace_id == llm.context.trace_id


def test_every_span_is_marked_acp_and_names_the_server(exported):
    trace = ACPTurnTrace(acp_server="gemini-cli", model_id="gemini-2.5-pro")
    trace.start_turn("go")
    entry = _tool_entry("call_1")
    trace.tool_started(entry)
    trace.tool_finished(entry)
    trace.finish_turn("done", "", [entry])

    spans = _by_type(exported(), "LLM") + _by_type(exported(), "TOOL")
    assert len(spans) == 2
    for span in spans:
        assert _meta(span, AGENT_KIND_METADATA_KEY) == "acp"
        assert _meta(span, ACP_SERVER_METADATA_KEY) == "gemini-cli"
        assert _meta(span, "acp_model") == "gemini-2.5-pro"


def test_tool_call_ids_survive_out_of_order_completion(exported):
    """Two calls open before either closes — each result must keep its own id."""
    trace = ACPTurnTrace(acp_server="codex", model_id=None)
    trace.start_turn("go")
    first, second = _tool_entry("call_a"), _tool_entry("call_b")
    trace.tool_started(first)
    trace.tool_started(second)
    trace.tool_finished(second)
    trace.tool_finished(first)
    trace.finish_turn("done", "", [first, second])

    tools = _by_type(exported(), "TOOL")
    pairs = {_meta(t, "tool_call_id"): _tool_text(t) for t in tools}
    assert pairs == {
        "call_a": "contents of call_a",
        "call_b": "contents of call_b",
    }


def test_abandon_closes_a_tool_span_left_open_by_a_failed_turn(exported):
    trace = ACPTurnTrace(acp_server="codex", model_id=None)
    trace.start_turn("go")
    trace.tool_started(_tool_entry("call_1", status="in_progress"))
    trace.abandon()

    # An unended span is never exported at all — the result would vanish.
    assert len(_by_type(exported(), "TOOL")) == 1
    assert len(_by_type(exported(), "LLM")) == 1


def test_finish_turn_closes_a_tool_call_the_server_never_terminated(exported):
    trace = ACPTurnTrace(acp_server="codex", model_id=None)
    trace.start_turn("go")
    entry = _tool_entry("call_1", status="in_progress")
    trace.tool_started(entry)
    trace.finish_turn("done", "", [entry])

    (tool,) = _by_type(exported(), "TOOL")
    assert _tool_text(tool) == "contents of call_1"


def test_tracing_is_inert_when_observability_is_disabled(monkeypatch, exported):
    monkeypatch.setattr(
        "openhands.sdk.agent.acp_tracing.should_enable_observability",
        lambda: False,
    )
    trace = ACPTurnTrace(acp_server="codex", model_id=None)
    trace.start_turn("go")
    entry = _tool_entry("call_1")
    trace.tool_started(entry)
    trace.tool_finished(entry)
    trace.finish_turn("done", "", [entry])

    assert exported() == ()


def test_a_broken_span_backend_never_breaks_the_turn(monkeypatch, exported):
    """Observability failures must stay invisible to the agent."""
    import lmnr

    monkeypatch.setattr(
        lmnr.Laminar, "start_span", lambda **kw: (_ for _ in ()).throw(RuntimeError())
    )
    trace = ACPTurnTrace(acp_server="codex", model_id=None)
    trace.start_turn("go")
    entry = _tool_entry("call_1")
    trace.tool_started(entry)
    trace.tool_finished(entry)
    trace.finish_turn("done", "", [entry])
    trace.abandon()


def test_a_server_that_omits_raw_input_still_records_what_it_could(exported):
    """Codex sends no ``raw_input``; the title is the only signal of the call."""
    trace = ACPTurnTrace(acp_server="codex", model_id=None)
    trace.start_turn("go")
    entry = _tool_entry("call_1", raw_input=None, title="Read file '/a/b.py'")
    trace.tool_started(entry)
    trace.tool_finished(entry)
    trace.finish_turn("done", "", [entry])

    (llm,) = _by_type(exported(), "LLM")
    (call,) = json.loads((llm.attributes or {})["lmnr.span.output"])[0]["tool_calls"]
    assert json.loads(call["function"]["arguments"]) == {"title": "Read file '/a/b.py'"}


def test_a_tool_starting_during_teardown_does_not_break_it(exported):
    """A timed-out turn tears down on the caller thread while the ACP portal
    thread can still deliver a ToolCallStart, so the open-span table is mutated
    mid-teardown. Deterministic here: the racing insert happens from inside the
    close callback rather than from a real thread."""
    trace = ACPTurnTrace(acp_server="codex", model_id=None)
    trace.start_turn("go")
    trace.tool_started(_tool_entry("call_1", status="in_progress"))

    original_close = ACPTurnTrace._close_tool_span
    raced: list[str] = []

    def racing_close(span, entry):
        if not raced:
            raced.append("x")
            trace.tool_started(_tool_entry("call_racer", status="in_progress"))
        original_close(span, entry)

    with patch.object(ACPTurnTrace, "_close_tool_span", staticmethod(racing_close)):
        trace.abandon()  # must not raise "dictionary changed size during iteration"

    trace.abandon()  # idempotent, and closes anything the race left open
    assert len(_by_type(exported(), "LLM")) == 1
