"""The synchronous ``/goal`` driver.

``run_goal`` is a thin synchronous driver over :class:`GoalController`: it owns
the I/O (sending messages, running the agent) while the controller owns the
judging and continue-vs-stop decision. An async agent-server task can reuse the
same controller with its own I/O loop.

Unlike a critic (which the run loop consults *inside* one ``run()``), this drives
the conversation from the outside, so it composes with whatever critic the agent
already has -- that critic governs each inner ``run()``; this loop governs the
overall objective.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openhands.sdk.conversation.goal.controller import (
    GoalController,
    GoalDone,
    GoalOutcome,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.llm import LLM


def run_goal(
    conversation: BaseConversation,
    objective: str,
    judge_llm: LLM,
    *,
    max_iterations: int = 10,
) -> GoalOutcome:
    """Drive ``conversation`` toward ``objective``, judging completion each round.

    Sends the objective, runs the agent to a finish, and lets a
    :class:`GoalController` decide whether to re-prompt with the judge's feedback
    or stop. Returns a :class:`GoalOutcome` whose ``status`` is ``"complete"`` or
    ``"capped"``.

    Args:
        conversation: The conversation to drive (any agent/critic config).
        objective: The goal to pursue and audit against.
        judge_llm: The second LLM that grades completion.
        max_iterations: Hard cap on audit rounds before giving up.
    """
    controller = GoalController(objective, judge_llm, max_iterations=max_iterations)
    conversation.send_message(controller.start())
    while True:
        conversation.run()
        step = controller.on_run_finished(conversation.state.events)
        if isinstance(step, GoalDone):
            return step.outcome
        conversation.send_message(step.followup)
