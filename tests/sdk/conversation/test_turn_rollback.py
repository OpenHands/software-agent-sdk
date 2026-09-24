"""Unit tests for turn-level rollback helpers (#5225)."""

from openhands.sdk.conversation.turn_rollback import (
    build_recovery_message,
    find_turn_start,
    summarize_completed_calls,
)
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    AgentErrorEvent,
    MessageEvent,
    ObservationEvent,
    UserRejectObservation,
)
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.tool.schema import Action, Observation


class TurnRollbackAction(Action):
    command: str = "noop"


class TurnRollbackObservation(Observation):
    result: str = "done"

    @property
    def agent_observation(self) -> list[TextContent]:
        return [TextContent(text=self.result)]


def _message(event_id: str) -> MessageEvent:
    return MessageEvent(
        id=event_id,
        llm_message=Message(role="user", content=[TextContent(text=event_id)]),
        source="user",
    )


def _action(event_id: str, response_id: str, tool_name: str = "bash") -> ActionEvent:
    return ActionEvent(
        id=event_id,
        thought=[TextContent(text="thinking")],
        action=TurnRollbackAction(),
        tool_name=tool_name,
        tool_call_id=f"call_{event_id}",
        tool_call=MessageToolCall(
            id=f"call_{event_id}",
            name=tool_name,
            arguments="{}",
            origin="completion",
        ),
        llm_response_id=response_id,
    )


def _observation(event_id: str, action_id: str) -> ObservationEvent:
    return ObservationEvent(
        id=event_id,
        action_id=action_id,
        observation=TurnRollbackObservation(),
        tool_name="bash",
        tool_call_id=f"call_{action_id}",
    )


def test_finds_single_action_turn_and_its_predecessor():
    branch = [_message("m1"), _action("a1", "resp1"), _observation("o1", "a1")]

    target, actions = find_turn_start(branch)

    assert target == "m1"
    assert [a.id for a in actions] == ["a1"]


def test_parallel_calls_from_one_response_roll_back_together():
    """Actions sharing an llm_response_id must unwind as a unit, or a tool_use
    is left without its tool_result."""
    branch = [
        _message("m1"),
        _action("a1", "resp1"),
        _action("a2", "resp1"),
        _action("a3", "resp1"),
        _observation("o1", "a1"),
        _observation("o2", "a2"),
        _observation("o3", "a3"),
    ]

    target, actions = find_turn_start(branch)

    assert target == "m1"
    assert [a.id for a in actions] == ["a1", "a2", "a3"]


def test_parallel_calls_interleaved_with_results_still_roll_back_together():
    """Observations may land between the actions of one turn. Scanning back
    only until the first non-action would find just the last call and orphan
    the rest."""
    branch = [
        _message("m1"),
        _action("a1", "resp1"),
        _observation("o1", "a1"),
        _action("a2", "resp1"),
        _observation("o2", "a2"),
    ]

    target, actions = find_turn_start(branch)

    assert target == "m1"
    assert [a.id for a in actions] == ["a1", "a2"]


def test_only_the_latest_turn_is_selected():
    branch = [
        _message("m1"),
        _action("a1", "resp1"),
        _observation("o1", "a1"),
        _action("a2", "resp2"),
        _observation("o2", "a2"),
    ]

    target, actions = find_turn_start(branch)

    assert target == "o1"
    assert [a.id for a in actions] == ["a2"]


def test_turn_at_branch_root_has_no_predecessor():
    branch = [_action("a1", "resp1"), _observation("o1", "a1")]

    target, actions = find_turn_start(branch)

    assert target is None
    assert [a.id for a in actions] == ["a1"]


def test_branch_without_actions_yields_nothing_to_roll_back():
    target, actions = find_turn_start([_message("m1"), _message("m2")])

    assert target is None
    assert actions == []


def test_summary_reports_each_call_outcome():
    a1 = _action("a1", "resp1", tool_name="bash")
    a2 = _action("a2", "resp1", tool_name="browser")
    a3 = _action("a3", "resp1", tool_name="editor")
    branch = [
        a1,
        a2,
        a3,
        _observation("o1", "a1"),
        UserRejectObservation(
            id="r2",
            action_id="a2",
            tool_name="browser",
            tool_call_id="call_a2",
            rejection_reason="user said no",
        ),
        AgentErrorEvent(
            id="e3",
            tool_name="editor",
            tool_call_id="call_a3",
            error="boom",
        ),
    ]

    assert summarize_completed_calls(branch, [a1, a2, a3]) == [
        "bash (succeeded)",
        "browser (rejected by user)",
        "editor (failed)",
    ]


def test_summary_truncates_very_wide_turns():
    actions = [_action(f"a{i}", "resp1") for i in range(15)]

    summaries = summarize_completed_calls(actions, actions)

    assert len(summaries) == 11
    assert summaries[-1] == "and 5 more"


def test_recovery_message_states_error_and_surviving_side_effects():
    text = build_recovery_message(
        "messages.148.content.0: invalid base64",
        ["bash (succeeded)", "browser (failed)"],
    )

    assert "invalid base64" in text
    assert "bash (succeeded)" in text
    assert "browser (failed)" in text
    # The agent must know side effects outlived the rolled-back turn.
    assert "still exist" in text


def test_recovery_message_without_completed_calls_omits_side_effect_section():
    text = build_recovery_message("boom", [])

    assert "boom" in text
    assert "still exist" not in text
