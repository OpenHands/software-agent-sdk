"""Unit tests for the ``ask_user`` tool schema, validation, and answer parsing."""

import pytest

from openhands.sdk.event import ActionEvent
from openhands.sdk.llm import MessageToolCall
from openhands.sdk.tool.builtins import AskUserTool
from openhands.sdk.tool.builtins.ask_user import (
    AskUserAction,
    AskUserAnswerAction,
    AskUserAnswerPayload,
    AskUserSelection,
    OptionInfo,
    QuestionInfo,
    build_ask_user_observation,
    parse_ask_user_answer,
    validate_questions,
)
from openhands.sdk.tool.spec import Tool


def _question(
    header: str = "auth",
    options: list[OptionInfo] | None = None,
    multi_select: bool = False,
) -> QuestionInfo:
    return QuestionInfo(
        question="Which auth?",
        header=header,
        options=options
        or [
            OptionInfo(id="jwt", label="JWT", description="token auth"),
            OptionInfo(id="session", label="Session", description="cookie auth"),
        ],
        multiSelect=multi_select,
    )


def _action_event(action: AskUserAction) -> ActionEvent:
    return ActionEvent(
        source="agent",
        thought=[],
        action=action,
        tool_name=AskUserTool.name,
        tool_call_id="call_1",
        tool_call=MessageToolCall(
            id="call_1",
            name=AskUserTool.name,
            arguments="{}",
            origin="completion",
        ),
        llm_response_id="resp_1",
    )


def test_tool_registered_and_pauses():
    from openhands.sdk.tool.builtins import BUILT_IN_TOOL_CLASSES

    assert BUILT_IN_TOOL_CLASSES[AskUserTool.__name__] is AskUserTool
    tool = AskUserTool.create()[0]
    assert tool.pauses_run_for_user_input is True
    assert tool.name == "ask_user"
    assert tool.action_type is AskUserAction


def test_tool_is_opt_in_not_default():
    """ask_user must be requested explicitly, never auto-attached."""
    from openhands.sdk.tool.defaults import resolve_tool_specs

    default_names = {
        spec.name for spec in resolve_tool_specs(None, enable_browser=True)
    }
    assert AskUserTool.name not in default_names


def test_tool_resolves_from_spec():
    from openhands.sdk.tool.registry import resolve_tool

    resolved = resolve_tool(Tool(name=AskUserTool.name), None)  # type: ignore[arg-type]
    assert [t.name for t in resolved] == [AskUserTool.name]


@pytest.mark.parametrize(
    "action, expected",
    [
        (AskUserAction(questions=[]), "at least one question"),
        (
            AskUserAction(questions=[_question(header="")]),
            "non-empty header",
        ),
        (
            AskUserAction(questions=[_question(header="a" * 13)]),
            "longer than 12 characters",
        ),
        (
            AskUserAction(questions=[_question(), _question()]),
            "duplicate question header",
        ),
        (
            AskUserAction(
                questions=[
                    _question(
                        options=[
                            OptionInfo(id="x", label="X", description="x"),
                        ]
                    )
                ]
            ),
            "between 2 and 4 options",
        ),
        (
            AskUserAction(
                questions=[
                    _question(
                        options=[
                            OptionInfo(id="x", label="X", description="x"),
                            OptionInfo(id="x", label="Y", description="y"),
                        ]
                    )
                ]
            ),
            "duplicate option id",
        ),
        (
            AskUserAction(
                questions=[
                    _question(
                        options=[
                            OptionInfo(id="x", label="Same", description="x"),
                            OptionInfo(id="y", label="Same", description="y"),
                        ]
                    )
                ]
            ),
            "duplicate option label",
        ),
    ],
)
def test_validate_questions_rejects(action: AskUserAction, expected: str):
    error = validate_questions(action)
    assert error is not None
    assert expected in error


def test_validate_questions_accepts():
    assert validate_questions(AskUserAction(questions=[_question()])) is None


def test_parse_ask_user_answer_ignores_plain_text():
    assert parse_ask_user_answer("hello there") is None
    assert parse_ask_user_answer('{"not": "an answer"}') is None
    assert parse_ask_user_answer("{not json") is None


def test_parse_ask_user_answer_reads_payload():
    payload = parse_ask_user_answer(
        '{"request_id": "abc", "action": "decline", "answers": {}}'
    )
    assert payload is not None
    assert payload.request_id == "abc"
    assert payload.action is AskUserAnswerAction.DECLINE


def test_build_observation_matches_options():
    action = AskUserAction(questions=[_question()])
    event = _action_event(action)
    payload = AskUserAnswerPayload(
        request_id=event.id,
        action=AskUserAnswerAction.ACCEPT,
        answers={"auth": [AskUserSelection(option_id="jwt")]},
    )
    observation, error = build_ask_user_observation(event, payload)
    assert error is None
    assert observation is not None
    assert observation.request_id == event.id
    assert observation.answers[0].selected[0].option_id == "jwt"
    assert observation.answers[0].selected[0].label == "JWT"
    assert not observation.is_error


def test_build_observation_allows_free_text():
    action = AskUserAction(questions=[_question()])
    event = _action_event(action)
    payload = AskUserAnswerPayload(
        request_id=event.id,
        action=AskUserAnswerAction.ACCEPT,
        answers={"auth": [AskUserSelection(label="something else")]},
    )
    observation, error = build_ask_user_observation(event, payload)
    assert error is None
    assert observation is not None
    selected = observation.answers[0].selected[0]
    assert selected.option_id is None
    assert selected.label == "something else"


def test_build_observation_reports_mismatched_request_id():
    action = AskUserAction(questions=[_question()])
    event = _action_event(action)
    payload = AskUserAnswerPayload(
        request_id="other", action=AskUserAnswerAction.ACCEPT
    )
    observation, error = build_ask_user_observation(event, payload)
    assert observation is None
    assert error is not None
    assert "does not match" in error


def test_build_observation_reports_unknown_option():
    action = AskUserAction(questions=[_question()])
    event = _action_event(action)
    payload = AskUserAnswerPayload(
        request_id=event.id,
        action=AskUserAnswerAction.ACCEPT,
        answers={"auth": [AskUserSelection(option_id="nope")]},
    )
    observation, error = build_ask_user_observation(event, payload)
    assert observation is None
    assert error is not None
    assert "unrecognized option id" in error


def test_build_observation_reports_unknown_header():
    action = AskUserAction(questions=[_question()])
    event = _action_event(action)
    payload = AskUserAnswerPayload(
        request_id=event.id,
        action=AskUserAnswerAction.ACCEPT,
        answers={"wrong": [AskUserSelection(option_id="jwt")]},
    )
    observation, error = build_ask_user_observation(event, payload)
    assert observation is None
    assert error is not None
    assert "unrecognized question header" in error


def test_build_observation_rejects_multi_select_on_single():
    action = AskUserAction(questions=[_question()])
    event = _action_event(action)
    payload = AskUserAnswerPayload(
        request_id=event.id,
        action=AskUserAnswerAction.ACCEPT,
        answers={
            "auth": [
                AskUserSelection(option_id="jwt"),
                AskUserSelection(option_id="session"),
            ]
        },
    )
    observation, error = build_ask_user_observation(event, payload)
    assert observation is None
    assert error is not None
    assert "single-select" in error


def test_build_observation_allows_multi_select():
    action = AskUserAction(questions=[_question(multi_select=True)])
    event = _action_event(action)
    payload = AskUserAnswerPayload(
        request_id=event.id,
        action=AskUserAnswerAction.ACCEPT,
        answers={
            "auth": [
                AskUserSelection(option_id="jwt"),
                AskUserSelection(option_id="session"),
            ]
        },
    )
    observation, error = build_ask_user_observation(event, payload)
    assert error is None
    assert observation is not None
    assert len(observation.answers[0].selected) == 2


def test_build_observation_decline_and_cancel():
    action = AskUserAction(questions=[_question()])
    event = _action_event(action)
    for resolution in (AskUserAnswerAction.DECLINE, AskUserAnswerAction.CANCEL):
        payload = AskUserAnswerPayload(request_id=event.id, action=resolution)
        observation, error = build_ask_user_observation(event, payload)
        assert error is None
        assert observation is not None
        assert observation.action is resolution
        assert observation.answers == []


def test_executor_without_answer_returns_corrective_error():
    """A direct execution (no answer) must not hang; it returns an error."""
    action = AskUserAction(questions=[_question()])
    executor = AskUserTool.create()[0].executor
    assert executor is not None
    observation = executor(action)
    assert observation.is_error
    assert observation.action is AskUserAnswerAction.CANCEL
