"""Tests for the /goal driver loop and command parsing."""

import pytest

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.base import BaseConversation
from openhands.sdk.conversation.goal import GoalOutcome, run_goal
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.testing import TestLLM


def _text_llm(*texts: str) -> TestLLM:
    """An LLM scripted to return content-only replies (each finishes a turn)."""
    return TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text=t)]) for t in texts]
    )


def _conversation(*agent_turns: str) -> BaseConversation:
    """A conversation whose agent finishes (content-only) on each run()."""
    agent = Agent(llm=_text_llm(*agent_turns), tools=[])
    return Conversation(agent=agent)


_DONE = '{"score": 1.0, "complete": true, "missing": ""}'
_NOT_DONE = '{"score": 0.2, "complete": false, "missing": "tests"}'


@pytest.mark.parametrize(
    ("agent_turns", "verdicts", "max_iterations", "status", "iterations"),
    [
        # judge says "done" on the first audit
        (("done",), (_DONE,), 5, "complete", 1),
        # "not done" then "done" -> loops once more, then completes
        (("turn 1", "turn 2"), (_NOT_DONE, _DONE), 5, "complete", 2),
        # never done -> capped at max_iterations
        (("turn 1", "turn 2"), (_NOT_DONE, _NOT_DONE), 2, "capped", 2),
    ],
    ids=["complete-first-audit", "loops-until-complete", "caps-at-max"],
)
def test_run_goal_outcomes(agent_turns, verdicts, max_iterations, status, iterations):
    conversation = _conversation(*agent_turns)
    outcome = run_goal(
        conversation, "build x", _text_llm(*verdicts), max_iterations=max_iterations
    )
    assert isinstance(outcome, GoalOutcome)
    assert outcome.status == status
    assert outcome.iterations == iterations
    assert outcome.verdict.complete is (status == "complete")


@pytest.mark.parametrize(
    ("objective", "max_iterations"),
    [("   ", 5), ("build x", 0)],
    ids=["empty-objective", "bad-max-iterations"],
)
def test_run_goal_rejects_invalid_input(objective, max_iterations):
    with pytest.raises(ValueError):
        run_goal(
            _conversation("noop"),
            objective,
            _text_llm("{}"),
            max_iterations=max_iterations,
        )
