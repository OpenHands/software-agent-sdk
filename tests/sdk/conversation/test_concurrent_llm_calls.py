"""Cheap load test: concurrent conversations must overlap LLM calls.

A process-wide lock around the model transport once forced every
conversation to wait on a single in-flight request (issue #4477). This
drives several ``LocalConversation.arun`` loops — the path the
agent-server uses — through ``LLM.acompletion`` and a delayed fake
transport. It runs with the rest of ``tests/sdk`` in the ``sdk-tests``
CI job. No paid model credentials.

Thresholds
----------
``N_CONVERSATIONS`` conversations each block for ``CALL_LATENCY_S``
inside the transport. Fully serial execution spans about ``N`` times one
call. Overlapped execution spans about one call, plus a little scheduler
skew.

``WALL_TIME_FACTOR`` is applied to the measured single-call span, not to
a fixed number of seconds, so a slow runner stretches the budget with
the reference. ``2.5`` still rejects a fully serial run (about ``4×``)
and leaves room for normal CI timer slack on an overlapped run (about
``1×``). The in-flight peak is the primary signal; wall-clock span is
the throughput check.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import pytest

from openhands.sdk import LLM, LocalConversation
from openhands.sdk.agent import Agent
from openhands.sdk.conversation.state import ConversationExecutionStatus
from tests.conftest import create_mock_litellm_response


# See module docstring. Keep these next to the assertions they bound.
N_CONVERSATIONS = 4
CALL_LATENCY_S = 0.4
WALL_TIME_FACTOR = 2.5


@dataclass(frozen=True, slots=True)
class _CallWindow:
    start: float
    end: float

    @property
    def span(self) -> float:
        return self.end - self.start


def _peak_inflight(windows: list[_CallWindow]) -> int:
    """Maximum number of transport calls active at the same instant.

    An end and a start at the same timestamp do not overlap: the call
    that finished is no longer in flight.
    """
    events: list[tuple[float, int]] = []
    for window in windows:
        events.append((window.start, 1))
        events.append((window.end, -1))
    events.sort(key=lambda item: (item[0], item[1]))
    active = 0
    peak = 0
    for _timestamp, delta in events:
        active += delta
        peak = max(peak, active)
    return peak


def _format_windows(windows: list[_CallWindow], origin: float) -> str:
    lines = []
    for index, window in enumerate(windows):
        lines.append(
            f"  call {index}: start={window.start - origin:.3f}s "
            f"end={window.end - origin:.3f}s span={window.span:.3f}s"
        )
    return "\n".join(lines)


def _make_conversation(tmp_path, index: int) -> LocalConversation:
    persist = tmp_path / f"conv-{index}"
    persist.mkdir()
    llm = LLM(
        model="gpt-4o-mini",
        api_key="test-key",
        usage_id=f"concurrent-{index}",
        num_retries=0,
    )
    agent = Agent(llm=llm, tools=[], include_default_tools=[])
    conversation = LocalConversation(
        agent=agent,
        workspace=str(tmp_path),
        persistence_dir=str(persist),
        visualizer=None,
    )
    conversation.send_message("hello")
    return conversation


async def _run_one(conversation: LocalConversation) -> ConversationExecutionStatus:
    await conversation.arun()
    return conversation.state.execution_status


@pytest.mark.asyncio
async def test_concurrent_conversations_overlap_llm_calls(tmp_path, monkeypatch):
    """Several conversations must issue overlapping transport calls.

    Fails when a global lock (or any other shared critical section held
    across the model wait) makes the calls run one after another.
    """
    windows: list[_CallWindow] = []

    async def delayed_transport(**kwargs):
        start = time.monotonic()
        await asyncio.sleep(CALL_LATENCY_S)
        windows.append(_CallWindow(start=start, end=time.monotonic()))
        return create_mock_litellm_response(
            content="done",
            model=kwargs.get("model", "gpt-4o-mini"),
            response_id=f"concurrent-{len(windows)}",
        )

    monkeypatch.setattr(
        "openhands.sdk.llm.llm.litellm_acompletion",
        delayed_transport,
    )

    reference = _make_conversation(tmp_path, 0)
    conversations = [
        _make_conversation(tmp_path, index) for index in range(1, N_CONVERSATIONS + 1)
    ]
    try:
        reference_status = await _run_one(reference)
        assert reference_status == ConversationExecutionStatus.FINISHED, (
            f"reference conversation ended {reference_status}"
        )
        assert len(windows) == 1, (
            f"reference run made {len(windows)} transport calls; expected 1"
        )
        reference_window = windows[0]

        concurrent_started = len(windows)
        statuses = await asyncio.gather(*[_run_one(conv) for conv in conversations])
        concurrent = windows[concurrent_started:]
    finally:
        reference.close()
        for conversation in conversations:
            conversation.close()

    failed = [
        (index, status)
        for index, status in enumerate(statuses)
        if status != ConversationExecutionStatus.FINISHED
    ]
    assert not failed, f"conversations did not finish: {failed}"
    assert len(concurrent) == N_CONVERSATIONS, (
        f"concurrent run made {len(concurrent)} transport calls; "
        f"expected {N_CONVERSATIONS}. A conversation that errored before "
        "the model call, or retried, changes the overlap measurement."
    )

    origin = min(window.start for window in concurrent)
    span = max(window.end for window in concurrent) - origin
    peak = _peak_inflight(concurrent)
    serial_estimate = reference_window.span * N_CONVERSATIONS
    budget = reference_window.span * WALL_TIME_FACTOR
    details = (
        f"peak in-flight={peak} (serialized would be 1), "
        f"concurrent span={span:.3f}s, "
        f"reference span={reference_window.span:.3f}s, "
        f"budget={budget:.3f}s "
        f"(reference × {WALL_TIME_FACTOR}), "
        f"serial estimate={serial_estimate:.3f}s "
        f"(reference × {N_CONVERSATIONS}).\n"
        f"{_format_windows(concurrent, origin)}\n"
        "LLM calls ran one after another. A lock held across the model "
        "wait would produce this."
    )

    assert peak >= 2, details
    assert span < budget, details
