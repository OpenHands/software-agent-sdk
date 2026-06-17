"""The transport-agnostic brain of the ``/goal`` loop.

``GoalController`` decides -- after each agent run finishes -- whether to
continue (with a followup message) or stop (with a ``GoalOutcome``). It performs
NO I/O: a *driver* (the sync ``run_goal``, or an async agent-server task) owns
sending messages and running the agent; the controller only judges and decides.
That split lets the sync and async drivers share identical decision logic.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field

from openhands.sdk.conversation.goal.judge import GoalVerdict, judge_goal
from openhands.sdk.conversation.goal.prompts import FOLLOWUP_PROMPT
from openhands.sdk.event import Event
from openhands.sdk.llm import LLM
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class GoalOutcome(BaseModel):
    """Result of a ``/goal`` loop.

    ``status`` distinguishes genuine completion from hitting the iteration cap,
    so a driver never has to guess whether a silent finish meant success.
    """

    status: Literal["complete", "capped"]
    iterations: int = Field(ge=1, description="Number of audit rounds performed.")
    verdict: GoalVerdict


GoalStatusName = Literal["running", "complete", "capped", "interrupted"]
"""Lifecycle state of a ``/goal`` loop."""


class GoalStatus(BaseModel):
    """Live status of a ``/goal`` loop, for a UI progress chip.

    The agent server publishes this as the ``value`` of a
    ``ConversationStateUpdateEvent`` with ``key="goal"`` at each lifecycle point
    (start, each round, and the terminal/interrupted state).
    """

    active: bool = Field(description="Whether the goal loop is still running.")
    status: GoalStatusName
    iteration: int = Field(ge=0, description="Audit rounds completed so far.")
    max_iterations: int = Field(ge=1)
    objective: str
    verdict: GoalVerdict | None = Field(
        default=None, description="Last judge verdict; set once the loop ends."
    )


class GoalContinue(BaseModel):
    """Decision to keep going: send ``followup`` before the next run."""

    followup: str


class GoalDone(BaseModel):
    """Decision to stop: the loop finished with ``outcome``."""

    outcome: GoalOutcome


GoalStep = GoalContinue | GoalDone
"""One decision returned by :meth:`GoalController.on_run_finished`."""


class GoalController:
    """Judges goal completion and decides continue-vs-stop, without doing I/O.

    A driver calls :meth:`start` once to get the first message to send, then
    calls :meth:`on_run_finished` after every agent run to get the next
    decision. The controller owns the iteration count and the ``max_iterations``
    cap, so drivers stay trivial.
    """

    def __init__(
        self, objective: str, judge_llm: LLM, *, max_iterations: int = 10
    ) -> None:
        if not objective.strip():
            raise ValueError("Goal objective must not be empty.")
        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1.")
        self.objective = objective
        self.judge_llm = judge_llm
        self.max_iterations = max_iterations
        self.iteration = 0

    def start(self) -> str:
        """Return the first message a driver should send (the objective)."""
        return self.objective

    def on_run_finished(self, events: Sequence[Event]) -> GoalStep:
        """Judge the objective after a run and decide whether to continue.

        Increments the iteration count, audits ``events`` with the judge LLM,
        and returns a :class:`GoalContinue` (with a followup) or a terminal
        :class:`GoalDone` (with a :class:`GoalOutcome`).
        """
        self.iteration += 1
        verdict = judge_goal(self.judge_llm, self.objective, events)
        logger.info(
            "Goal audit %d/%d: score=%.2f complete=%s",
            self.iteration,
            self.max_iterations,
            verdict.score,
            verdict.complete,
        )
        if verdict.complete:
            return GoalDone(
                outcome=GoalOutcome(
                    status="complete", iterations=self.iteration, verdict=verdict
                )
            )
        if self.iteration >= self.max_iterations:
            return GoalDone(
                outcome=GoalOutcome(
                    status="capped", iterations=self.iteration, verdict=verdict
                )
            )
        missing = verdict.missing or "Some requirements are not yet verified."
        followup = FOLLOWUP_PROMPT.format(iteration=self.iteration, missing=missing)
        return GoalContinue(followup=followup)
