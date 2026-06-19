"""Tests for the /goal driver loop in EventService (the agent-server side).

These drive a real EventService + LocalConversation with a scripted TestLLM
agent (each run finishes on a content-only reply) and a separate scripted judge.
"""

import asyncio
from threading import Event
from typing import cast
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import PrivateAttr, SecretStr

from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import StoredConversation
from openhands.sdk.agent import Agent
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.testing import TestLLM
from openhands.sdk.workspace import LocalWorkspace


def _scripted(*texts: str, usage_id: str) -> TestLLM:
    return TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text=t)]) for t in texts],
        usage_id=usage_id,
    )


class _GatedLLM(TestLLM):
    """Judge LLM that blocks until released, signalling when it has entered.

    Lets a test deterministically catch the goal loop mid-audit (no run in
    flight) and stop it.
    """

    _gate: Event = PrivateAttr(default_factory=Event)
    _entered: Event = PrivateAttr(default_factory=Event)

    def completion(
        self,
        messages,
        tools=None,
        add_security_risk_prediction=False,
        on_token=None,
        **kwargs,
    ):
        self._entered.set()
        self._gate.wait(timeout=10)
        return super().completion(
            messages,
            tools,
            add_security_risk_prediction,
            on_token,
            **kwargs,
        )


def _goal_status_updates(event_service: EventService) -> list:
    return [
        e.value
        for e in event_service.get_conversation()._state.events
        if isinstance(e, ConversationStateUpdateEvent) and e.key == "goal"
    ]


@pytest.fixture
def event_service(tmp_path):
    with patch("openhands.sdk.llm.utils.model_info.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(json=lambda: {"data": []})
        service = EventService(
            stored=StoredConversation(
                id=uuid4(),
                agent=Agent(
                    llm=LLM(
                        usage_id="agent", model="test-model", api_key=SecretStr("x")
                    ),
                    tools=[],
                ),
                workspace=LocalWorkspace(working_dir=str(tmp_path / "workspace")),
            ),
            conversations_dir=tmp_path / "conversations",
        )
        yield service


async def _start(service: EventService, tmp_path, *agent_turns: str) -> None:
    """Start the service and install a scripted agent LLM (one reply per run)."""
    (tmp_path / "workspace").mkdir(exist_ok=True)
    await service.start()
    service.get_conversation().switch_llm(_scripted(*agent_turns, usage_id="agent"))


_DONE = '{"score": 1.0, "complete": true, "missing": ""}'
_NOT_DONE = '{"score": 0.2, "complete": false, "missing": "tests"}'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agent_turns", "verdicts", "max_iterations", "status", "iterations"),
    [
        (("turn 1", "turn 2"), (_NOT_DONE, _DONE), 5, "complete", 2),
        (("turn 1", "turn 2"), (_NOT_DONE, _NOT_DONE), 2, "capped", 2),
    ],
    ids=["completes-after-two-rounds", "caps-at-max"],
)
async def test_goal_loop_outcomes(
    event_service, tmp_path, agent_turns, verdicts, max_iterations, status, iterations
):
    await _start(event_service, tmp_path, *agent_turns)
    judge = _scripted(*verdicts, usage_id="judge")
    try:
        await event_service.start_goal_loop(
            "build x", judge_llm=judge, max_iterations=max_iterations
        )
        await asyncio.wait_for(event_service._goal_loop_task, timeout=15)

        outcome = event_service._goal_loop_outcome
        assert outcome is not None
        assert outcome.status == status
        assert outcome.iterations == iterations
    finally:
        await event_service.close()


@pytest.mark.asyncio
async def test_goal_loop_emits_status_events(event_service, tmp_path):
    # The loop publishes ConversationStateUpdateEvent(key="goal") at each
    # lifecycle point; they are persisted to the shared event log (and streamed
    # to subscribers) so a UI can render a progress chip.
    await _start(event_service, tmp_path, "did the work")
    judge = _scripted(
        '{"score": 1.0, "complete": true, "missing": ""}', usage_id="judge"
    )
    try:
        await event_service.start_goal_loop(
            "build x", judge_llm=judge, max_iterations=3
        )
        await asyncio.wait_for(event_service._goal_loop_task, timeout=15)

        updates = [
            e.value
            for e in event_service.get_conversation()._state.events
            if isinstance(e, ConversationStateUpdateEvent) and e.key == "goal"
        ]
        assert updates, "expected goal-status events"
        # First update: running + active. Last: complete + inactive.
        assert updates[0]["active"] is True
        assert updates[0]["status"] == "running"
        assert updates[-1]["active"] is False
        assert updates[-1]["status"] == "complete"
        assert updates[-1]["iteration"] == 1
        assert updates[-1]["objective"] == "build x"
    finally:
        await event_service.close()


@pytest.mark.asyncio
async def test_goal_loop_emits_per_round_verdicts(event_service, tmp_path):
    # Each continuing round publishes its judge verdict (score + missing) on the
    # running status event, so a UI can show per-round feedback, not just the
    # terminal verdict.
    await _start(event_service, tmp_path, "turn 1", "turn 2")
    judge = _scripted(_NOT_DONE, _DONE, usage_id="judge")
    try:
        await event_service.start_goal_loop(
            "build x", judge_llm=judge, max_iterations=5
        )
        await asyncio.wait_for(event_service._goal_loop_task, timeout=15)

        updates = _goal_status_updates(event_service)
        # The kickoff update (iteration 0) has no verdict yet.
        kickoff = next(u for u in updates if u["iteration"] == 0)
        assert kickoff["verdict"] is None
        # The mid-loop "running" update for round 1 carries the round's verdict.
        round_one = next(
            u for u in updates if u["status"] == "running" and u["iteration"] == 1
        )
        assert round_one["verdict"] is not None
        assert round_one["verdict"]["score"] == 0.2
        assert round_one["verdict"]["missing"] == "tests"
    finally:
        await event_service.close()


@pytest.mark.asyncio
async def test_goal_loop_defaults_judge_to_agent_llm(event_service, tmp_path):
    # No judge_llm passed -> the agent's own LLM is used as the judge, so its
    # scripted queue serves both the agent turn and the verdict. This is the
    # path the POST /goal endpoint always takes.
    await _start(
        event_service,
        tmp_path,
        "did the work",
        '{"score": 1.0, "complete": true, "missing": ""}',
    )
    try:
        await event_service.start_goal_loop("build x", max_iterations=3)
        await asyncio.wait_for(event_service._goal_loop_task, timeout=15)

        outcome = event_service._goal_loop_outcome
        assert outcome is not None
        assert outcome.status == "complete"
        assert outcome.iterations == 1
    finally:
        await event_service.close()


@pytest.mark.asyncio
async def test_start_goal_loop_rejects_empty_objective(event_service, tmp_path):
    await _start(event_service, tmp_path, "noop")
    judge = _scripted("{}", usage_id="judge")
    try:
        with pytest.raises(ValueError):
            await event_service.start_goal_loop("   ", judge_llm=judge)
    finally:
        await event_service.close()


@pytest.mark.asyncio
async def test_start_goal_loop_rejects_concurrent_goal_loop(event_service, tmp_path):
    await _start(event_service, tmp_path, "noop")
    judge = _scripted("{}", usage_id="judge")
    try:
        # Occupy the goal loop slot with a task that won't finish on its own.
        event_service._goal_loop_task = asyncio.create_task(asyncio.sleep(10))
        with pytest.raises(ValueError, match="goal_already_running"):
            await event_service.start_goal_loop("build x", judge_llm=judge)
    finally:
        event_service._goal_loop_task.cancel()
        event_service._goal_loop_task = None
        await event_service.close()


@pytest.mark.asyncio
async def test_stop_goal_loop_when_idle_returns_false(event_service, tmp_path):
    await _start(event_service, tmp_path, "noop")
    try:
        assert await event_service.stop_goal_loop() is False
    finally:
        await event_service.close()


@pytest.mark.asyncio
async def test_user_message_stops_running_goal_loop(event_service, tmp_path):
    # A user message on the normal chat path cancels a running goal loop before
    # being processed.
    await _start(event_service, tmp_path, "noop")
    try:
        event_service._goal_loop_task = asyncio.create_task(asyncio.sleep(30))
        await event_service.send_message(
            Message(role="user", content=[TextContent(text="hello")]), run=False
        )
        assert event_service._goal_loop_task.done()
    finally:
        await event_service.close()


@pytest.mark.asyncio
async def test_stop_running_goal_loop_emits_interrupted(event_service, tmp_path):
    await _start(event_service, tmp_path, "did the work")
    judge = cast(
        _GatedLLM,
        _GatedLLM.from_messages(
            [
                Message(
                    role="assistant",
                    content=[TextContent(text='{"score": 0.2, "complete": false}')],
                )
            ],
            usage_id="judge",
        ),
    )
    try:
        await event_service.start_goal_loop(
            "build x", judge_llm=judge, max_iterations=5
        )
        # Wait until the judge is blocked: the goal is mid-audit, no run in flight.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, judge._entered.wait, 5.0)

        assert await event_service.stop_goal_loop() is True
        updates = _goal_status_updates(event_service)
        assert updates[-1]["status"] == "interrupted"
        assert updates[-1]["active"] is False
        assert updates[-1]["iteration"] == 1
    finally:
        judge._gate.set()  # release the orphaned judge thread for cleanup
        await event_service.close()


@pytest.mark.asyncio
async def test_resume_from_interrupted_status(event_service, tmp_path):
    await _start(event_service, tmp_path, "resumed and finished")
    # Simulate a goal loop that was interrupted at round 1 of 5.
    conversation = event_service.get_conversation()
    with conversation._state:
        conversation._on_event(
            ConversationStateUpdateEvent(
                key="goal",
                value={
                    "active": False,
                    "status": "interrupted",
                    "iteration": 1,
                    "max_iterations": 5,
                    "objective": "build x",
                    "verdict": None,
                },
            )
        )
    judge = _scripted(
        '{"score": 1.0, "complete": true, "missing": ""}', usage_id="judge"
    )
    try:
        await event_service.resume_goal_loop(judge_llm=judge)
        await asyncio.wait_for(event_service._goal_loop_task, timeout=15)

        outcome = event_service._goal_loop_outcome
        assert outcome is not None
        assert outcome.status == "complete"
        assert outcome.iterations == 2  # resumed from round 1 -> completed at round 2
    finally:
        await event_service.close()


@pytest.mark.asyncio
async def test_resume_without_resumable_goal_loop_raises(event_service, tmp_path):
    await _start(event_service, tmp_path, "noop")
    judge = _scripted("{}", usage_id="judge")
    try:
        with pytest.raises(ValueError, match="no_resumable_goal"):
            await event_service.resume_goal_loop(judge_llm=judge)
    finally:
        await event_service.close()


@pytest.mark.asyncio
async def test_resume_after_completed_goal_loop_raises(event_service, tmp_path):
    # A completed (or capped) goal loop is not resumable.
    await _start(event_service, tmp_path, "noop")
    conversation = event_service.get_conversation()
    with conversation._state:
        conversation._on_event(
            ConversationStateUpdateEvent(
                key="goal",
                value={
                    "active": False,
                    "status": "complete",
                    "iteration": 2,
                    "max_iterations": 5,
                    "objective": "build x",
                    "verdict": None,
                },
            )
        )
    judge = _scripted("{}", usage_id="judge")
    try:
        with pytest.raises(ValueError, match="no_resumable_goal"):
            await event_service.resume_goal_loop(judge_llm=judge)
    finally:
        await event_service.close()


@pytest.mark.asyncio
async def test_goal_loop_halts_on_run_error_as_interrupted(event_service, tmp_path):
    # Simulate "out of credits": the agent's run raises. The goal loop must record an
    # interrupted (resumable) status, not die silently with no outcome.
    (tmp_path / "workspace").mkdir(exist_ok=True)
    await event_service.start()
    event_service.get_conversation().switch_llm(
        TestLLM.from_messages([RuntimeError("out of credits")], usage_id="agent")
    )
    judge = _scripted(
        '{"score": 0.1, "complete": false, "missing": "x"}', usage_id="judge"
    )
    try:
        await event_service.start_goal_loop(
            "build x", judge_llm=judge, max_iterations=5
        )
        await asyncio.wait_for(event_service._goal_loop_task, timeout=15)

        updates = _goal_status_updates(event_service)
        assert updates[-1]["status"] == "interrupted"
        assert updates[-1]["active"] is False
        assert event_service._goal_loop_outcome is None
    finally:
        await event_service.close()


@pytest.mark.asyncio
async def test_goal_loop_emits_interrupted_on_unexpected_error(event_service, tmp_path):
    # A judge LLM that *raises* (e.g. a network error) crashes the loop via the
    # generic `except Exception` path -- distinct from a run error surfaced as
    # ConversationExecutionStatus.ERROR (test above). The loop must still record
    # a terminal interrupted (resumable) status; otherwise the last persisted
    # event stays active=True/running and the UI shows a dead goal as running.
    await _start(event_service, tmp_path, "did the work")
    judge = TestLLM.from_messages(
        [RuntimeError("judge network error")], usage_id="judge"
    )
    try:
        await event_service.start_goal_loop(
            "build x", judge_llm=judge, max_iterations=5
        )
        await asyncio.wait_for(event_service._goal_loop_task, timeout=15)

        updates = _goal_status_updates(event_service)
        assert updates[-1]["status"] == "interrupted"
        assert updates[-1]["active"] is False
        assert event_service._goal_loop_outcome is None
    finally:
        await event_service.close()


@pytest.mark.asyncio
async def test_start_goal_loop_rejected_while_run_active(event_service, tmp_path):
    # /goal must refuse with conversation_already_running (-> 409) when a normal
    # run is already in flight, instead of slipping in beside it and judging that
    # run's unrelated transcript. A placeholder _run_task stands in for the active
    # run (same pattern as test_start_goal_loop_rejects_concurrent_goal_loop).
    await _start(event_service, tmp_path, "noop")
    judge = _scripted("{}", usage_id="judge")
    try:
        event_service._run_task = asyncio.create_task(asyncio.sleep(10))
        with pytest.raises(ValueError, match="conversation_already_running"):
            await event_service.start_goal_loop("build x", judge_llm=judge)
        assert event_service._goal_loop_task is None
    finally:
        event_service._run_task.cancel()
        event_service._run_task = None
        await event_service.close()


@pytest.mark.asyncio
async def test_resume_goal_loop_rejected_while_run_active(event_service, tmp_path):
    # Resume uses the same busy guard; reuse the placeholder _run_task approach.
    await _start(event_service, tmp_path, "noop")
    conversation = event_service.get_conversation()
    with conversation._state:
        conversation._on_event(
            ConversationStateUpdateEvent(
                key="goal",
                value={
                    "active": False,
                    "status": "interrupted",
                    "iteration": 1,
                    "max_iterations": 5,
                    "objective": "build x",
                    "verdict": None,
                },
            )
        )
    judge = _scripted("{}", usage_id="judge")
    try:
        event_service._run_task = asyncio.create_task(asyncio.sleep(10))
        with pytest.raises(ValueError, match="conversation_already_running"):
            await event_service.resume_goal_loop(judge_llm=judge)
    finally:
        event_service._run_task.cancel()
        event_service._run_task = None
        await event_service.close()
