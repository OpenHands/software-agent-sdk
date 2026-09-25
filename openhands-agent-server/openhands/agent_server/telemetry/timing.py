"""Wall-clock per-operation timing with an in-flight "stuck" watchdog.

Wrap a coroutine body in :func:`timed_operation` to emit elapsed wall-clock
duration on completion, plus a ``stuck`` signal if the operation exceeds its
budget. The watchdog runs as a separate task and never blocks the measured
operation, so a wedged operation stays observable. Emission is best-effort and
resolves the sink/factory lazily so the live consent decision is honored.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Final

from openhands.agent_server.telemetry import models as m
from openhands.agent_server.telemetry.service import (
    get_event_factory,
    get_telemetry_sink,
)
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

#: Budget applied when a call site does not supply its own expected elapsed time.
DEFAULT_STUCK_BUDGET_MS: Final[int] = 20_000


@dataclass(frozen=True, slots=True)
class OperationTimingResult:
    """What a measurement site produced. Emitted verbatim as event properties."""

    operation: str
    duration_ms: int
    stuck: bool
    stuck_budget_ms: int
    evicted_count: str | None = None
    """Bucketed magnitude for ops that report one (e.g. evictions closed)."""


def _default_emitter(result: OperationTimingResult) -> None:
    """Emit one :class:`OperationTimingProperties` event, best-effort.

    Resolves the process sink/factory at emit time so consent changes are
    honored and a pre-init NoOp sink is never cached. Never raises: telemetry
    must never break the measured operation.
    """
    sink = get_telemetry_sink()
    if not sink.enabled:
        return
    factory = get_event_factory()
    if factory is None:
        return
    try:
        properties = m.OperationTimingProperties(
            operation=result.operation,
            duration_ms=result.duration_ms,
            stuck=result.stuck,
            stuck_budget_ms=result.stuck_budget_ms,
            evicted_count=result.evicted_count,
        )
        sink.emit(factory.build(m.EventName.OPERATION_TIMING, properties))
    except Exception:
        logger.debug("telemetry_operation_timing_failed", exc_info=True)


#: Tests swap this to capture events without touching the process sink.
DEFAULT_EMITTER: Callable[[OperationTimingResult], None] = _default_emitter


def _clamp_ms(seconds: float) -> int:
    return min(round(seconds * 1000), m.MAX_DURATION_MS)


class OperationTimer:
    """Timing state shared between the measured body and the watchdog task."""

    def __init__(
        self,
        operation: str,
        budget_ms: int,
        emit: Callable[[OperationTimingResult], None],
    ) -> None:
        self.operation = operation
        self.budget_ms = budget_ms
        self._emit = emit
        self._started = time.monotonic()
        self.stuck = False
        # Bucketed magnitude attached by the measured body before exit.
        self.evicted_count: str | None = None

    @property
    def duration_ms(self) -> int:
        return _clamp_ms(time.monotonic() - self._started)

    def _emit_now(self, stuck: bool) -> None:
        self._emit(
            OperationTimingResult(
                operation=self.operation,
                duration_ms=self.duration_ms,
                stuck=stuck,
                stuck_budget_ms=self.budget_ms,
                evicted_count=self.evicted_count,
            )
        )

    async def _watchdog(self) -> None:
        await asyncio.sleep(self.budget_ms / 1000.0)
        if self.stuck:
            return
        self.stuck = True
        self._emit_now(stuck=True)


@asynccontextmanager
async def timed_operation(
    operation: str,
    *,
    budget_ms: int | None = None,
    emit: Callable[[OperationTimingResult], None] | None = None,
) -> AsyncIterator[OperationTimer]:
    """Time ``operation`` in wall-clock ms with a stuck watchdog.

    Emits at most one ``stuck`` event (if the budget elapses while the body is
    still running) and always one completion event on exit. A deadlock
    therefore produces a stuck event with no completion event, while a merely
    slow operation produces both — the two failure shapes stay distinguishable.
    The watchdog is a separate task and never blocks the body.

    ``budget_ms`` is the expected elapsed time for this specific operation;
    when omitted, :data:`DEFAULT_STUCK_BUDGET_MS` (20s) applies. ``emit`` is
    injectable for tests and defaults to the process-wide emitter.
    """
    emitter = emit if emit is not None else DEFAULT_EMITTER
    timer = OperationTimer(operation, budget_ms or DEFAULT_STUCK_BUDGET_MS, emitter)
    watchdog = asyncio.create_task(timer._watchdog())
    try:
        yield timer
    finally:
        watchdog.cancel()
        with suppress(asyncio.CancelledError):
            await watchdog
        timer._emit_now(stuck=timer.stuck)
