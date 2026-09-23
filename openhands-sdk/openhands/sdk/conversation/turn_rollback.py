"""Roll back the turn that poisoned a conversation.

Some provider rejections indict content that is already persisted in the
history: an image whose bytes do not decode, an unsupported attachment, a
tool result the provider will not read. Re-sending the same history
reproduces the failure exactly, so the conversation cannot progress.

Rolling the offending turn out of view lets the agent try again. The events
stay in the log -- only the active branch moves -- so nothing is lost for
audit, and the agent is told what happened so it can course-correct.
"""

from __future__ import annotations

from collections.abc import Sequence

from openhands.sdk.event.base import Event, EventID
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    AgentErrorEvent,
    ObservationEvent,
    UserRejectObservation,
)
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

_MAX_SUMMARIZED_CALLS = 10


def find_turn_start(
    branch: Sequence[Event],
) -> tuple[EventID | None, list[ActionEvent]]:
    """Locate the last turn on ``branch``.

    A turn is every ``ActionEvent`` sharing the most recent ``llm_response_id``
    -- one LLM response may emit several parallel tool calls, and they must be
    unwound together or a ``tool_use`` is orphaned from its ``tool_result``.

    Returns the id of the event *preceding* the turn (the rollback target,
    ``None`` when the turn starts at the root) and the turn's actions. Returns
    ``(None, [])`` when the branch holds no action to roll back.
    """
    last_action_idx = next(
        (
            i
            for i in range(len(branch) - 1, -1, -1)
            if isinstance(branch[i], ActionEvent)
        ),
        None,
    )
    if last_action_idx is None:
        return None, []

    last_action = branch[last_action_idx]
    assert isinstance(last_action, ActionEvent)
    response_id = last_action.llm_response_id

    # Scan the whole branch rather than walking back until a non-action: the
    # turn's actions and their observations may be interleaved, so an early
    # break would find only the last call and orphan the rest.
    matches = [
        (i, e)
        for i, e in enumerate(branch)
        if isinstance(e, ActionEvent) and e.llm_response_id == response_id
    ]
    first_idx = matches[0][0]
    actions = [e for _, e in matches]
    target = branch[first_idx - 1].id if first_idx > 0 else None
    return target, actions


def summarize_completed_calls(
    branch: Sequence[Event], actions: Sequence[ActionEvent]
) -> list[str]:
    """Describe each tool call in the rolled-back turn and how it ended.

    The calls already ran. Their side effects -- files written, commits made --
    survive the rollback, so the agent needs to know about work it can no
    longer see in context.
    """
    outcome_by_action: dict[EventID, str] = {}
    outcome_by_tool_call: dict[str, str] = {}
    for event in branch:
        if isinstance(event, ObservationEvent):
            outcome_by_action[event.action_id] = "succeeded"
        elif isinstance(event, UserRejectObservation):
            outcome_by_action[event.action_id] = "rejected by user"
        elif isinstance(event, AgentErrorEvent):
            outcome_by_tool_call[event.tool_call_id] = "failed"

    summaries = []
    for action in actions[:_MAX_SUMMARIZED_CALLS]:
        outcome = outcome_by_action.get(
            action.id,
            outcome_by_tool_call.get(action.tool_call_id, "outcome unknown"),
        )
        summaries.append(f"{action.tool_name} ({outcome})")

    remaining = len(actions) - len(summaries)
    if remaining > 0:
        summaries.append(f"and {remaining} more")
    return summaries


def build_recovery_message(error_text: str, completed_calls: Sequence[str]) -> str:
    """Explain the rollback to the agent."""
    lines = [
        "The model provider rejected the previous turn because of content it "
        "could not read, so that turn has been removed from the conversation "
        "history and cannot be retried as-is.",
        "",
        f"Provider error: {error_text}",
    ]
    if completed_calls:
        lines += [
            "",
            "These tool calls had already run before the failure. Any changes "
            "they made (files, commits, external state) still exist even "
            "though the turn is no longer visible:",
            *(f"- {call}" for call in completed_calls),
        ]
    lines += [
        "",
        "Take a different approach. Avoid re-sending the content that was "
        "rejected -- for example, if a screenshot or attachment could not be "
        "read, continue without it. If you cannot make progress, explain the "
        "situation and ask for help.",
    ]
    return "\n".join(lines)
