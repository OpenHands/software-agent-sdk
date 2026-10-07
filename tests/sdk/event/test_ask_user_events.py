"""Tests for the ask_user request/response event pair and its schema."""

from openhands.sdk.event import (
    ASK_USER_TIMEOUT_SOURCE,
    AskUserAnswer,
    AskUserRequestEvent,
    AskUserResponseEvent,
    QuestionInfo,
    QuestionOption,
)


def _question() -> QuestionInfo:
    return QuestionInfo(
        id="auth",
        question="Which auth strategy?",
        options=[
            QuestionOption(id="jwt", label="JWT bearer tokens"),
            QuestionOption(id="session", label="Server sessions"),
        ],
    )


def test_request_event_serializes_typed_request_id():
    request = AskUserRequestEvent(
        request_id="req-1",
        questions=[_question()],
        action_id="action-1",
        tool_call_id="call-1",
    )

    assert request.source == "agent"
    assert request.tool_name == "ask_user"
    dumped = request.model_dump()
    assert dumped["kind"] == "AskUserRequestEvent"
    assert dumped["request_id"] == "req-1"
    assert dumped["questions"][0]["options"][0] == {
        "id": "jwt",
        "label": "JWT bearer tokens",
        "description": None,
    }


def test_response_event_round_trips_accept_answers():
    response = AskUserResponseEvent(
        request_id="req-1",
        action="accept",
        answers={"auth": AskUserAnswer(option_id="jwt", label="JWT bearer tokens")},
    )

    assert response.source == "user"
    restored = AskUserResponseEvent.model_validate(response.model_dump())
    assert restored.action == "accept"
    assert restored.answers["auth"].option_id == "jwt"


def test_response_event_rejects_unknown_action():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AskUserResponseEvent(request_id="req-1", action="approve")  # type: ignore[arg-type]


def test_response_event_defaults_answers_to_empty():
    response = AskUserResponseEvent(request_id="req-1", action="decline")

    assert response.answers == {}
    assert ASK_USER_TIMEOUT_SOURCE == "environment"
