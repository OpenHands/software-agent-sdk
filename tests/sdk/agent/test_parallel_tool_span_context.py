"""Trace-context propagation into ``ParallelToolExecutor`` worker threads.

Neither ``ThreadPoolExecutor.submit`` nor ``loop.run_in_executor`` copies
``contextvars`` by itself, so a dispatched tool call inherits nothing from the
dispatching thread unless the executor copies it explicitly.

Two configurations behave differently and are covered separately here:

* **lmnr not initialized** — nothing crosses the thread boundary at all, so a
  span opened in the worker starts its own trace.
* **lmnr initialized** (the ``lmnr_initialized`` fixture) — lmnr's vendored
  ``ThreadingInstrumentor`` patches ``ThreadPoolExecutor.submit`` to carry its
  own *isolated* context across, which is what ``@observe`` parents against, so
  TOOL spans already nest there without our copy. What lmnr does **not** carry
  is the OpenTelemetry *global* context or ordinary ``ContextVar``s; those gaps
  are what the copy closes, and the ``with_lmnr`` tests pin them.
"""

import asyncio
import contextvars
import inspect
import threading
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, Self
from unittest.mock import MagicMock, patch

import pytest
from litellm import ChatCompletionMessageToolCall
from litellm.types.utils import (
    Choices,
    Function,
    Message as LiteLLMMessage,
    ModelResponse,
)
from opentelemetry.context import Context, create_key, get_value, set_value
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanContext, Tracer
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.agent.parallel_executor import ParallelToolExecutor
from openhands.sdk.conversation import Conversation
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.tool import Action, Observation, Tool, ToolExecutor, register_tool
from openhands.sdk.tool.tool import ToolDefinition


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


_PROBE: contextvars.ContextVar[str] = contextvars.ContextVar("probe", default="unset")

_BARRIER_TIMEOUT = 10.0


@pytest.fixture
def probe() -> Iterator[contextvars.ContextVar[str]]:
    token = _PROBE.set("set-by-dispatcher")
    try:
        yield _PROBE
    finally:
        _PROBE.reset(token)


@pytest.fixture
def tracing() -> Iterator[tuple[Tracer, InMemorySpanExporter]]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    try:
        yield provider.get_tracer("test"), exporter
    finally:
        provider.shutdown()


@pytest.fixture
def lmnr_initialized() -> Iterator[None]:
    """Bring up lmnr's real ``TracerWrapper``, as ``maybe_init_laminar()`` does.

    Its side effects are process-wide monkeypatches, so tear them down again.
    """
    from lmnr.opentelemetry_lib.opentelemetry.instrumentation.threading import (
        ThreadingInstrumentor,
    )
    from lmnr.opentelemetry_lib.tracing import TracerWrapper

    if TracerWrapper.verify_initialized():
        yield
        return

    original_thread_init = threading.Thread.__init__
    TracerWrapper(
        exporter=InMemorySpanExporter(),
        disable_batch=True,
        instruments=set(),
        set_global_tracer_provider=False,
    )
    try:
        yield
    finally:
        ThreadingInstrumentor().uninstrument()
        threading.Thread.__init__ = original_thread_init  # type: ignore[method-assign]
        TracerWrapper._original_thread_init = None
        del TracerWrapper.instance


def _assert_lmnr_wraps_submit() -> None:
    """Guard: these tests are vacuous if lmnr's submit patch is not live."""
    assert hasattr(ThreadPoolExecutor.submit, "__wrapped__"), (
        "lmnr's ThreadingInstrumentor did not patch ThreadPoolExecutor.submit; "
        "the with_lmnr tests would silently degrade to the without_lmnr case"
    )


def _make_action(tool_call_id: str, tool_name: str = "my_tool") -> Any:
    ae = MagicMock()
    ae.tool_name = tool_name
    ae.tool_call_id = tool_call_id
    return ae


def _finished(exporter: InMemorySpanExporter, name: str) -> ReadableSpan:
    matches = [s for s in exporter.get_finished_spans() if s.name == name]
    assert len(matches) == 1, f"expected exactly one {name!r} span, got {matches}"
    return matches[0]


# ── contextvars reach the worker thread ───────────────────────────


def test_execute_batch_propagates_contextvars(probe) -> None:
    executor = ParallelToolExecutor(max_workers=2)
    seen: list[str] = []
    threads: set[str] = set()

    def tool_runner(action: Any) -> list[Any]:
        seen.append(probe.get())
        threads.add(threading.current_thread().name)
        return [MagicMock()]

    executor.execute_batch([_make_action("c0"), _make_action("c1")], tool_runner)

    assert threading.current_thread().name not in threads
    assert seen == ["set-by-dispatcher", "set-by-dispatcher"]


def test_aexecute_batch_propagates_contextvars(probe) -> None:
    executor = ParallelToolExecutor(max_workers=2)
    seen: list[str] = []
    threads: set[str] = set()

    def tool_runner(action: Any) -> list[Any]:
        seen.append(probe.get())
        threads.add(threading.current_thread().name)
        return [MagicMock()]

    asyncio.run(
        executor.aexecute_batch([_make_action("c0"), _make_action("c1")], tool_runner)
    )

    assert threading.current_thread().name not in threads
    assert seen == ["set-by-dispatcher", "set-by-dispatcher"]


# ── each task needs its own Context object ────────────────────────
# A single contextvars.Context cannot be entered by two threads at once
# (``RuntimeError: cannot enter context: ... is already entered``), so these
# force genuine overlap via a Barrier.


def test_execute_batch_overlapping_tasks_each_get_a_fresh_context(probe) -> None:
    executor = ParallelToolExecutor(max_workers=3)
    barrier = threading.Barrier(3)
    seen: list[str] = []
    lock = threading.Lock()

    def tool_runner(action: Any) -> list[Any]:
        barrier.wait(timeout=_BARRIER_TIMEOUT)
        with lock:
            seen.append(probe.get())
        return ["ok"]

    results = executor.execute_batch(
        [_make_action(f"c{i}", f"tool_{i}") for i in range(3)], tool_runner
    )

    assert results == [["ok"], ["ok"], ["ok"]]
    assert seen == ["set-by-dispatcher"] * 3


def test_aexecute_batch_overlapping_tasks_each_get_a_fresh_context(probe) -> None:
    executor = ParallelToolExecutor(max_workers=3)
    barrier = threading.Barrier(3)
    seen: list[str] = []
    lock = threading.Lock()

    def tool_runner(action: Any) -> list[Any]:
        barrier.wait(timeout=_BARRIER_TIMEOUT)
        with lock:
            seen.append(probe.get())
        return ["ok"]

    results = asyncio.run(
        executor.aexecute_batch(
            [_make_action(f"c{i}", f"tool_{i}") for i in range(3)], tool_runner
        )
    )

    assert results == [["ok"], ["ok"], ["ok"]]
    assert seen == ["set-by-dispatcher"] * 3


# ── OTel spans created in the worker nest under the dispatcher ────
# lmnr not initialized: nothing crosses the thread boundary without the copy.


def _span_nesting_runner(
    tracer: Tracer, recorded: list[SpanContext], lock: threading.Lock
) -> Any:
    def tool_runner(action: Any) -> list[Any]:
        with tracer.start_as_current_span(f"tool-{action.tool_call_id}") as span:
            with lock:
                recorded.append(span.get_span_context())
        return [MagicMock()]

    return tool_runner


def test_execute_batch_worker_spans_nest_under_dispatcher_without_lmnr(
    tracing,
) -> None:
    tracer, exporter = tracing
    executor = ParallelToolExecutor(max_workers=2)
    recorded: list[SpanContext] = []
    tool_runner = _span_nesting_runner(tracer, recorded, threading.Lock())

    with tracer.start_as_current_span("agent.step") as parent:
        parent_ctx = parent.get_span_context()
        executor.execute_batch(
            [_make_action("c0", "tool_0"), _make_action("c1", "tool_1")], tool_runner
        )

    assert {c.trace_id for c in recorded} == {parent_ctx.trace_id}
    for tool_call_id in ("c0", "c1"):
        span = _finished(exporter, f"tool-{tool_call_id}")
        assert span.parent is not None
        assert span.parent.span_id == parent_ctx.span_id


def test_aexecute_batch_worker_spans_nest_under_dispatcher_without_lmnr(
    tracing,
) -> None:
    tracer, exporter = tracing
    executor = ParallelToolExecutor(max_workers=2)
    recorded: list[SpanContext] = []
    tool_runner = _span_nesting_runner(tracer, recorded, threading.Lock())

    async def main() -> SpanContext:
        with tracer.start_as_current_span("agent.astep") as parent:
            await executor.aexecute_batch(
                [_make_action("c0", "tool_0"), _make_action("c1", "tool_1")],
                tool_runner,
            )
            return parent.get_span_context()

    parent_ctx = asyncio.run(main())

    assert {c.trace_id for c in recorded} == {parent_ctx.trace_id}
    for tool_call_id in ("c0", "c1"):
        span = _finished(exporter, f"tool-{tool_call_id}")
        assert span.parent is not None
        assert span.parent.span_id == parent_ctx.span_id


# ── What lmnr's own instrumentation leaves uncovered ──────────────
# lmnr patches ThreadPoolExecutor.submit to carry its isolated context, so
# @observe spans nest across the boundary with or without this executor's
# copy. The OTel global context and plain ContextVars are not carried; these
# pin that the copy closes those two gaps in lmnr's own configuration.


def test_execute_batch_worker_spans_nest_under_dispatcher_with_lmnr(
    tracing, lmnr_initialized
) -> None:
    _assert_lmnr_wraps_submit()
    tracer, exporter = tracing
    executor = ParallelToolExecutor(max_workers=2)
    recorded: list[SpanContext] = []
    tool_runner = _span_nesting_runner(tracer, recorded, threading.Lock())

    with tracer.start_as_current_span("agent.step") as parent:
        parent_ctx = parent.get_span_context()
        executor.execute_batch(
            [_make_action("c0", "tool_0"), _make_action("c1", "tool_1")], tool_runner
        )

    assert {c.trace_id for c in recorded} == {parent_ctx.trace_id}
    for tool_call_id in ("c0", "c1"):
        span = _finished(exporter, f"tool-{tool_call_id}")
        assert span.parent is not None
        assert span.parent.span_id == parent_ctx.span_id


def test_aexecute_batch_worker_spans_nest_under_dispatcher_with_lmnr(
    tracing, lmnr_initialized
) -> None:
    _assert_lmnr_wraps_submit()
    tracer, exporter = tracing
    executor = ParallelToolExecutor(max_workers=2)
    recorded: list[SpanContext] = []
    tool_runner = _span_nesting_runner(tracer, recorded, threading.Lock())

    async def main() -> SpanContext:
        with tracer.start_as_current_span("agent.astep") as parent:
            await executor.aexecute_batch(
                [_make_action("c0", "tool_0"), _make_action("c1", "tool_1")],
                tool_runner,
            )
            return parent.get_span_context()

    parent_ctx = asyncio.run(main())

    assert {c.trace_id for c in recorded} == {parent_ctx.trace_id}
    for tool_call_id in ("c0", "c1"):
        span = _finished(exporter, f"tool-{tool_call_id}")
        assert span.parent is not None
        assert span.parent.span_id == parent_ctx.span_id


def test_execute_batch_propagates_contextvars_with_lmnr(
    probe, lmnr_initialized
) -> None:
    _assert_lmnr_wraps_submit()
    executor = ParallelToolExecutor(max_workers=2)
    seen: list[str] = []
    lock = threading.Lock()

    def tool_runner(action: Any) -> list[Any]:
        with lock:
            seen.append(probe.get())
        return [MagicMock()]

    executor.execute_batch([_make_action("c0"), _make_action("c1")], tool_runner)

    assert seen == ["set-by-dispatcher", "set-by-dispatcher"]


def test_lmnr_isolated_context_survives_the_context_copy(lmnr_initialized) -> None:
    """The copy must not clobber what lmnr already propagates for itself."""
    from lmnr.opentelemetry_lib.tracing.context import (
        attach_context,
        detach_context,
        get_current_context,
    )

    key = create_key("openhands.test.isolated")
    executor = ParallelToolExecutor(max_workers=2)
    seen: list[Any] = []
    lock = threading.Lock()

    def tool_runner(action: Any) -> list[Any]:
        with lock:
            seen.append(get_value(key, get_current_context()))
        return [MagicMock()]

    token = attach_context(set_value(key, "isolated-parent", Context()))
    try:
        executor.execute_batch([_make_action("c0"), _make_action("c1")], tool_runner)
    finally:
        detach_context(token)

    assert seen == ["isolated-parent", "isolated-parent"]


# ── End-to-end through a real Agent with tool_concurrency_limit=2 ──
# ``observe`` is replaced by a raw-OTel stand-in, so this exercises the
# no-lmnr path end to end. With lmnr live the real ``observe`` parents against
# lmnr's isolated context, which lmnr carries across the boundary itself.


class _SpanCtxAction(Action):
    value: str = ""


class _SpanCtxObservation(Observation):
    result: str = ""


class _SpanCtxExecutor(ToolExecutor[_SpanCtxAction, _SpanCtxObservation]):
    def __call__(
        self, action: _SpanCtxAction, conversation=None
    ) -> _SpanCtxObservation:
        return _SpanCtxObservation(result=action.value)


class _SpanCtxToolA(ToolDefinition[_SpanCtxAction, _SpanCtxObservation]):
    name = "echo_a"

    @classmethod
    def create(cls, conv_state: "ConversationState | None" = None) -> Sequence[Self]:
        return [
            cls(
                description="Echoes its input",
                action_type=_SpanCtxAction,
                observation_type=_SpanCtxObservation,
                executor=_SpanCtxExecutor(),
            )
        ]


class _SpanCtxToolB(_SpanCtxToolA):
    name = "echo_b"


register_tool("SpanCtxEchoToolA", _SpanCtxToolA)
register_tool("SpanCtxEchoToolB", _SpanCtxToolB)


def _response_with_two_tool_calls() -> ModelResponse:
    return ModelResponse(
        id="mock-response-1",
        choices=[
            Choices(
                index=0,
                message=LiteLLMMessage(
                    role="assistant",
                    content="calling both tools",
                    tool_calls=[
                        ChatCompletionMessageToolCall(
                            id=f"call_{name}",
                            type="function",
                            function=Function(name=name, arguments='{"value": "hi"}'),
                        )
                        for name in ("echo_a", "echo_b")
                    ],
                ),
                finish_reason="tool_calls",
            )
        ],
        created=0,
        model="test-model",
        object="chat.completion",
    )


def test_agent_step_tool_spans_nest_under_dispatcher_without_lmnr(tracing) -> None:
    tracer, exporter = tracing
    llm = LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )
    agent = Agent(
        llm=llm,
        tools=[Tool(name="SpanCtxEchoToolA"), Tool(name="SpanCtxEchoToolB")],
        tool_concurrency_limit=2,
    )
    conversation = Conversation(agent=agent, callbacks=[])
    worker_threads: set[str] = set()

    def fake_observe(**kwargs: Any) -> Any:
        def decorator(fn: Any) -> Any:
            def wrapper(*args: Any, **fkwargs: Any) -> Any:
                worker_threads.add(threading.current_thread().name)
                with tracer.start_as_current_span("tool.execute"):
                    return fn(*args, **fkwargs)

            return wrapper

        return decorator

    with (
        patch(
            "openhands.sdk.llm.llm.litellm_completion",
            side_effect=lambda messages, **kw: _response_with_two_tool_calls(),
        ),
        patch(
            "openhands.sdk.agent.agent.should_enable_observability", return_value=True
        ),
        # Run the undecorated ``step``. Its ``@observe`` wrapper builds a real
        # lmnr span once observability is on anywhere in the process — and caches
        # it — which interposes between this test's parent span and the tool
        # spans. Without this, ambient ``LMNR_*`` env vars decide whether the test
        # measures what its name claims.
        patch.object(Agent, "step", inspect.unwrap(Agent.step)),
        patch("openhands.sdk.agent.agent.observe", side_effect=fake_observe),
    ):
        conversation.send_message(
            Message(role="user", content=[TextContent(text="please echo hi")])
        )
        with tracer.start_as_current_span("agent.step") as parent:
            parent_ctx = parent.get_span_context()
            agent.step(conversation, on_event=lambda e: None)

    assert threading.current_thread().name not in worker_threads, (
        "tool calls did not run off the dispatching thread; "
        "the test is not exercising the parallel path"
    )
    tool_spans = [s for s in exporter.get_finished_spans() if s.name == "tool.execute"]
    assert len(tool_spans) == 2
    for span in tool_spans:
        assert span.context is not None
        assert span.context.trace_id == parent_ctx.trace_id
        assert span.parent is not None
        assert span.parent.span_id == parent_ctx.span_id
