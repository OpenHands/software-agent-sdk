"""Tests for GoalController (the transport-agnostic /goal decision logic)."""

import pytest

from openhands.sdk.conversation.goal import (
    GoalContinue,
    GoalController,
    GoalDone,
)
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.testing import TestLLM


def _judge(*texts: str) -> TestLLM:
    """A judge LLM scripted to return one verdict per on_run_finished() call."""
    return TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text=t)]) for t in texts]
    )


def test_start_returns_objective():
    controller = GoalController("build x", _judge("{}"))
    assert controller.start() == "build x"
    assert controller.iteration == 0


def test_continue_when_incomplete():
    controller = GoalController(
        "build x",
        _judge('{"score": 0.2, "complete": false, "missing": "tests"}'),
        max_iterations=3,
    )
    step = controller.on_run_finished([])
    assert isinstance(step, GoalContinue)
    assert "tests" in step.followup
    assert controller.iteration == 1


def test_done_when_complete():
    controller = GoalController(
        "build x", _judge('{"score": 1.0, "complete": true, "missing": ""}')
    )
    step = controller.on_run_finished([])
    assert isinstance(step, GoalDone)
    assert step.outcome.status == "complete"
    assert step.outcome.iterations == 1
    assert step.outcome.verdict.complete


def test_caps_at_max_iterations():
    incomplete = '{"score": 0.1, "complete": false, "missing": "still broken"}'
    controller = GoalController(
        "build x", _judge(incomplete, incomplete), max_iterations=2
    )

    assert isinstance(controller.on_run_finished([]), GoalContinue)  # round 1
    step = controller.on_run_finished([])  # round 2 -> capped
    assert isinstance(step, GoalDone)
    assert step.outcome.status == "capped"
    assert step.outcome.iterations == 2
    assert not step.outcome.verdict.complete


@pytest.mark.parametrize(
    ("objective", "max_iterations"),
    [("   ", 10), ("build x", 0)],
    ids=["empty-objective", "bad-max-iterations"],
)
def test_validates_inputs(objective, max_iterations):
    with pytest.raises(ValueError):
        GoalController(objective, _judge("{}"), max_iterations=max_iterations)
