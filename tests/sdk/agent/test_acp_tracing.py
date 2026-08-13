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
    ACPTurnUsage,
)


METADATA_PREFIX = "lmnr.association.properties.metadata."

# Spelled out rather than imported: these are a wire contract with Laminar, so a
# rename in the SDK must fail here rather than silently follow along.
INPUT_TOKENS = "gen_ai.usage.input_tokens"
OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
CACHE_READ_TOKENS = "gen_ai.usage.cache_read_input_tokens"
CACHE_WRITE_TOKENS = "gen_ai.usage.cache_creation_input_tokens"
REASONING_TOKENS = "gen_ai.usage.reasoning_tokens"
TOTAL_TOKENS = "llm.usage.total_tokens"
COST = "gen_ai.usage.cost"
REQUEST_MODEL = "gen_ai.request.model"
RESPONSE_MODEL = "gen_ai.response.model"
PROVIDER = "gen_ai.system"


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


def _attrs(span) -> dict:
    return dict(span.attributes or {})


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
    trace = ACPTurnTrace(acp_server="codex", model_id="gpt-5.5")
    trace.start_turn("go")
    entry = _tool_entry("call_1")
    trace.tool_started(entry)
    trace.tool_finished(entry)
    trace.finish_turn("done", "", [entry], usage=ACPTurnUsage(100, 50, cost=0.05))

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


@pytest.mark.asyncio
async def test_a_call_that_starts_terminal_closes_at_that_notification(exported):
    """Some servers report a terminal status on the very first notification, so no
    later transition arrives. Closing only at ``finish_turn`` would bill the rest of
    the turn to that tool call. Driven through ``session_update`` so the wiring in
    ``acp_agent`` is what is under test, not just ``ACPTurnTrace``.
    """
    from unittest.mock import MagicMock

    from acp.schema import ToolCallStart

    from openhands.sdk.agent.acp_agent import _OpenHandsACPBridge

    start = MagicMock(spec=ToolCallStart)
    start.tool_call_id = "tc-terminal"
    start.title = "git status"
    start.kind = "execute"
    start.status = "completed"  # terminal on the very first notification
    start.raw_input = {"command": "git status"}
    start.raw_output = "nothing to commit"
    start.content = None

    client = _OpenHandsACPBridge()
    client.on_event = lambda _event: None
    client.trace = ACPTurnTrace(acp_server="codex", model_id=None)
    client.trace.start_turn("go")

    await client.session_update("s1", start)

    # Already exported, i.e. ended — before the turn is finished at all.
    (tool,) = _by_type(exported(), "TOOL")
    assert _meta(tool, "tool_call_id") == "tc-terminal"
    assert tool.end_time is not None
    closed_at = tool.end_time

    client.trace.finish_turn("done", "", client.accumulated_tool_calls)

    (llm,) = _by_type(exported(), "LLM")
    assert tool.end_time == closed_at, "finish_turn must not re-close the span"
    assert llm.end_time is not None and closed_at <= llm.end_time


def test_an_oversized_block_prompt_is_capped(exported):
    """Production passes ``prompt_blocks`` — a list of ACP content blocks, not a
    string — so a cap that only understood strings never applied to the real
    prompt, and one base64 image block can be megabytes."""
    from acp.schema import TextContentBlock

    blocks = [
        TextContentBlock(text="describe this", type="text"),
        TextContentBlock(
            text="A" * 400_000, type="text"
        ),  # stands in for base64 image data
    ]
    trace = ACPTurnTrace(acp_server="claude-code", model_id=None)
    trace.start_turn(blocks)
    trace.finish_turn("done", "", [])

    (llm,) = _by_type(exported(), "LLM")
    recorded = (llm.attributes or {})["lmnr.span.input"]
    assert len(recorded) < 200_000, "oversized prompt reached the backend uncapped"
    assert "[truncated]" in recorded
    assert "describe this" in recorded  # the head of the prompt survives


def test_a_normal_block_prompt_is_recorded_unchanged(exported):
    from acp.schema import TextContentBlock

    trace = ACPTurnTrace(acp_server="claude-code", model_id=None)
    trace.start_turn([TextContentBlock(text="read the file", type="text")])
    trace.finish_turn("done", "", [])

    (llm,) = _by_type(exported(), "LLM")
    recorded = (llm.attributes or {})["lmnr.span.input"]
    assert "read the file" in recorded
    assert "[truncated]" not in recorded


def test_a_secret_in_the_prompt_is_masked_before_it_is_recorded(exported):
    """The prompt is the user's own text, so it can carry a pasted credential."""
    from acp.schema import TextContentBlock

    secret = "ghp_averyrealisticlookingtoken0123456789"

    def mask(text: str) -> str:
        return text.replace(secret, "<secret-hidden>")

    trace = ACPTurnTrace(acp_server="claude-code", model_id=None, mask=mask)
    trace.start_turn(
        [TextContentBlock(text=f"deploy using {secret} please", type="text")]
    )
    trace.finish_turn("done", "", [])

    (llm,) = _by_type(exported(), "LLM")
    recorded = (llm.attributes or {})["lmnr.span.input"]
    assert secret not in recorded
    assert "<secret-hidden>" in recorded
    assert "deploy using" in recorded


def test_the_prompt_is_dropped_rather_than_recorded_raw_if_masking_fails(exported):
    def broken_mask(text: str) -> str:
        raise RuntimeError("masker unavailable")

    trace = ACPTurnTrace(acp_server="claude-code", model_id=None, mask=broken_mask)
    trace.start_turn("deploy using ghp_secret please")
    trace.finish_turn("done", "", [])

    (llm,) = _by_type(exported(), "LLM")
    assert "ghp_secret" not in str((llm.attributes or {}).get("lmnr.span.input"))


def _finished_turn(exported, usage: ACPTurnUsage | None, **kwargs):
    """Run one bare turn and return the exported LLM span's attributes."""
    trace = ACPTurnTrace(acp_server=kwargs.pop("acp_server", "claude-code"), **kwargs)
    trace.start_turn("go")
    trace.finish_turn("done", "", [], usage=usage)
    (llm,) = _by_type(exported(), "LLM")
    return _attrs(llm)


def test_turn_span_carries_the_token_counts_the_acp_server_reported(exported):
    attrs = _finished_turn(
        exported,
        ACPTurnUsage(input_tokens=100, output_tokens=50),
        model_id="claude-opus-5",
    )

    assert attrs[INPUT_TOKENS] == 100
    assert attrs[OUTPUT_TOKENS] == 50


def test_turn_span_counts_cache_tokens_outside_the_input_count(exported):
    """ACP reports cache reads outside ``input_tokens``, where litellm counts
    them inside it — so the total is a sum of all four, not input+output. A
    claude-code turn is mostly cache reads, so getting this wrong understates
    the billed work by orders of magnitude."""
    attrs = _finished_turn(
        exported,
        ACPTurnUsage(
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=50_000,
            cache_write_tokens=2_000,
        ),
        model_id="claude-opus-5",
    )

    assert attrs[CACHE_READ_TOKENS] == 50_000
    assert attrs[CACHE_WRITE_TOKENS] == 2_000
    assert attrs[TOTAL_TOKENS] == 52_150


def test_turn_span_omits_reasoning_tokens_from_the_total_it_reports(exported):
    """Providers bill thinking as output, so reasoning is already counted."""
    attrs = _finished_turn(
        exported,
        ACPTurnUsage(input_tokens=100, output_tokens=50, reasoning_tokens=20),
        model_id="claude-opus-5",
    )

    assert attrs[REASONING_TOKENS] == 20
    assert attrs[TOTAL_TOKENS] == 150


def test_turn_span_omits_token_attributes_when_the_server_reported_none(exported):
    """gemini-cli reports no usage today. A present ``0`` would assert the turn
    was free, hiding that the number is simply unknown."""
    attrs = _finished_turn(exported, ACPTurnUsage(), model_id="auto")

    for key in (INPUT_TOKENS, OUTPUT_TOKENS, TOTAL_TOKENS, REASONING_TOKENS):
        assert key not in attrs


def test_turn_span_carries_the_cost_the_agent_recorded_for_the_turn(exported):
    attrs = _finished_turn(
        exported,
        ACPTurnUsage(input_tokens=100, output_tokens=50, cost=0.07),
        model_id="claude-opus-5",
    )

    assert attrs[COST] == pytest.approx(0.07)


def test_turn_span_omits_cost_when_the_cli_reports_none(exported):
    """Absent means unknown, not free — and leaves the backend free to price it."""
    attrs = _finished_turn(
        exported,
        ACPTurnUsage(input_tokens=100, output_tokens=50),
        model_id="claude-opus-5",
    )

    assert COST not in attrs


def test_turn_span_names_the_model_before_the_turn_produces_any_usage(exported):
    """Model identity is stamped at turn start, so a turn that times out and is
    abandoned still says which model it ran on."""
    trace = ACPTurnTrace(acp_server="claude-code", model_id="opus[1m]")
    trace.start_turn("go")
    trace.abandon()

    (llm,) = _by_type(exported(), "LLM")
    assert _attrs(llm)[REQUEST_MODEL] == "opus[1m]"
    assert _attrs(llm)[RESPONSE_MODEL] == "opus[1m]"


@pytest.mark.parametrize(
    "acp_server,provider",
    [("claude-code", "anthropic"), ("codex", "openai"), ("gemini-cli", "gemini")],
)
def test_turn_span_names_the_provider_the_acp_cli_fronts(
    exported, acp_server, provider
):
    """These exact strings are what lmnr's LiteLLM instrumentation infers for the
    native path, so ACP spans aggregate with native ones."""
    attrs = _finished_turn(exported, None, acp_server=acp_server, model_id=None)

    assert attrs[PROVIDER] == provider


def test_a_custom_acp_server_leaves_the_provider_unset(exported):
    """A guessed provider would send a consumer to the wrong price table."""
    attrs = _finished_turn(exported, None, acp_server="custom", model_id=None)

    assert PROVIDER not in attrs


def test_the_turn_span_records_no_response_id(exported):
    """The only per-turn id ACP offers is the session id, which is effectively a
    bearer token. Completing lmnr's 'minimum LLM attribute set' with it would
    ship a credential to the tracing backend."""
    attrs = _finished_turn(
        exported, ACPTurnUsage(input_tokens=1, output_tokens=1), model_id="sonnet"
    )

    assert "gen_ai.response.id" not in attrs


def test_usage_attributes_the_span_rejects_never_break_the_turn(exported):
    trace = ACPTurnTrace(acp_server="codex", model_id="gpt-5.5")
    trace.start_turn("go")
    span = trace._turn_span
    with patch.object(
        type(span),
        "set_attributes",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("rejected")),
    ):
        trace.finish_turn("done", "", [], usage=ACPTurnUsage(100, 50, cost=0.05))

    # The turn still lands: output set, span ended and therefore exported.
    (llm,) = _by_type(exported(), "LLM")
    assert json.loads(_attrs(llm)["lmnr.span.output"])[0]["content"] == "done"
    assert INPUT_TOKENS not in _attrs(llm), "the patch did not take effect"


def test_usage_reported_for_a_turn_that_never_started_is_dropped(exported):
    """``finish_turn`` after an abandoned turn must not resurrect a span."""
    trace = ACPTurnTrace(acp_server="codex", model_id="gpt-5.5")
    trace.start_turn("go")
    trace.abandon()
    trace.finish_turn("done", "", [], usage=ACPTurnUsage(100, 50, cost=0.05))

    (llm,) = _by_type(exported(), "LLM")
    assert INPUT_TOKENS not in _attrs(llm)
