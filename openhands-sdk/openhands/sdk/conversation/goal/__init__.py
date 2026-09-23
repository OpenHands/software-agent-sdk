"""The ``/goal`` command: judge-driven, self-continuing goal completion.

A conversation-level command (not a critic) that drives the agent toward an
objective: it sends the objective, runs the agent, judges completion with a
second LLM, and re-prompts until the goal is done or a cap is reached.

The decision logic lives in :class:`GoalController` (transport-agnostic, no
I/O); :func:`run_goal` is a thin synchronous driver over it. An async
agent-server task can reuse the same controller with its own I/O loop.

Usage::

    from openhands.sdk.conversation.goal import run_goal

    outcome = run_goal(conversation, "make pytest pass for mathx.py", judge_llm)
"""

from openhands.sdk.conversation.goal.controller import (
    GoalContinue,
    GoalController,
    GoalDone,
    GoalOutcome,
    GoalStatus,
    GoalStatusName,
    GoalStep,
)
from openhands.sdk.conversation.goal.judge import GoalVerdict, judge_goal
from openhands.sdk.conversation.goal.runner import run_goal


__all__ = [
    "GoalContinue",
    "GoalController",
    "GoalDone",
    "GoalOutcome",
    "GoalStatus",
    "GoalStatusName",
    "GoalStep",
    "GoalVerdict",
    "judge_goal",
    "run_goal",
]
