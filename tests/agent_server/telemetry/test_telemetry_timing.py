"""Per-operation timing: the watchdog contract and the event model.

A deadlock must be distinguishable from a merely slow operation: a deadlock
produces a ``stuck`` event with no completion event, a slow operation produces
both, and a fast operation produces only the completion event.
"""

import asyncio

import pytest
from pydantic import ValidationError

from openhands.agent_server.telemetry import models as m
from openhands.agent_server.telemetry.timing import (
    DEFAULT_STUCK_BUDGET_MS,
    OperationTimingResult,
    timed_operation,
)


def test_default_stuck_budget_is_20_seconds():
    assert DEFAULT_STUCK_BUDGET_MS == 20_000


@pytest.mark.asyncio
async def test_fast_operation_emits_single_completion_event():
    events: list[OperationTimingResult] = []

    async with timed_operation(
        "conversation_create", budget_ms=1000, emit=events.append
    ):
        await asyncio.sleep(0.01)

    assert len(events) == 1
    (event,) = events
    assert event.operation == "conversation_create"
    assert event.stuck is False
    assert event.stuck_budget_ms == 1000
    assert event.duration_ms >= 0


@pytest.mark.asyncio
async def test_slow_operation_emits_stuck_then_completion():
    events: list[OperationTimingResult] = []

    async with timed_operation("conversation_delete", budget_ms=50, emit=events.append):
        await asyncio.sleep(0.2)

    assert len(events) == 2
    stuck, completion = events
    assert stuck.stuck is True
    assert stuck.duration_ms < completion.duration_ms
    assert completion.stuck is True
    assert completion.duration_ms >= stuck.duration_ms


@pytest.mark.asyncio
async def test_deadlock_emits_stuck_without_completion():
    """While the operation is still in flight, only the stuck event exists."""
    events: list[OperationTimingResult] = []
    cm = timed_operation("conversation_close", budget_ms=50, emit=events.append)
    await cm.__aenter__()

    await asyncio.sleep(0.1)

    # The watchdog fired; no completion event has been emitted yet, so the
    # operation looks deadlocked (stuck event present, completion absent).
    assert len(events) == 1
    assert events[0].stuck is True

    await cm.__aexit__(None, None, None)
    assert len(events) == 2
    assert events[1].stuck is True


@pytest.mark.asyncio
async def test_exception_still_emits_completion():
    events: list[OperationTimingResult] = []

    with pytest.raises(RuntimeError, match="boom"):
        async with timed_operation(
            "conversation_create", budget_ms=1000, emit=events.append
        ):
            raise RuntimeError("boom")

    assert len(events) == 1
    assert events[0].stuck is False


@pytest.mark.asyncio
async def test_timer_exposes_duration_to_the_measured_body():
    cm = timed_operation("conversation_create", budget_ms=1000, emit=lambda _: None)
    timer = await cm.__aenter__()
    try:
        assert timer.operation == "conversation_create"
        assert timer.budget_ms == 1000
        assert timer.duration_ms >= 0
        assert timer.stuck is False
    finally:
        await cm.__aexit__(None, None, None)


def test_timing_properties_reject_out_of_bounds_durations():
    m.OperationTimingProperties(
        operation="conversation_create",
        duration_ms=0,
        stuck=False,
        stuck_budget_ms=20_000,
    )
    with pytest.raises(ValidationError):
        m.OperationTimingProperties(
            operation="conversation_create",
            duration_ms=-1,
            stuck=False,
            stuck_budget_ms=20_000,
        )
    with pytest.raises(ValidationError):
        m.OperationTimingProperties(
            operation="conversation_create",
            duration_ms=0,
            stuck=False,
            stuck_budget_ms=-1,
        )
    # A leak shape (a path) cannot occupy the operation token.
    with pytest.raises(ValidationError):
        m.OperationTimingProperties(
            operation="/Users/alice/src/secret-project/main.py",
            duration_ms=5,
            stuck=False,
            stuck_budget_ms=20_000,
        )
