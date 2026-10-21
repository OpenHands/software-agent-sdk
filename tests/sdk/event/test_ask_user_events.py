"""Tests for the ask_user request/response event pair and its schema."""

import pytest

from openhands.sdk.event import (
    ASK_USER_TIMEOUT_SOURCE,
    AskUserAnswer,
    AskUserRequestError,
    AskUserRequestEvent,
    AskUserResponseEvent,
    QuestionInfo,
    QuestionOption,
)
from openhands.sdk.event.ask_user_schema import (
    normalize_ask_user_answers,
    validate_ask_user_answers,
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
        answers={"auth": [AskUserAnswer(option_id="jwt", label="JWT bearer tokens")]},
    )

    assert response.source == "user"
    restored = AskUserResponseEvent.model_validate(response.model_dump())
    assert restored.action == "accept"
    assert restored.answers["auth"][0].option_id == "jwt"


def test_response_event_round_trips_multi_select_answers():
    response = AskUserResponseEvent(
        request_id="req-1",
        action="accept",
        answers={
            "auth": [
                AskUserAnswer(option_id="jwt", label="JWT bearer tokens"),
                AskUserAnswer(option_id="session", label="Server sessions"),
            ]
        },
    )

    restored = AskUserResponseEvent.model_validate(response.model_dump())
    assert [a.option_id for a in restored.answers["auth"]] == ["jwt", "session"]


def test_response_event_rejects_unknown_action():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AskUserResponseEvent(request_id="req-1", action="approve")  # type: ignore[arg-type]


def test_response_event_defaults_answers_to_empty():
    response = AskUserResponseEvent(request_id="req-1", action="decline")

    assert response.answers == {}
    assert ASK_USER_TIMEOUT_SOURCE == "environment"


def test_normalize_accepts_single_model_sequence_and_mapping():
    assert normalize_ask_user_answers(
        {"auth": AskUserAnswer(option_id="jwt", label="JWT")}
    ) == {"auth": [AskUserAnswer(option_id="jwt", label="JWT")]}
    assert normalize_ask_user_answers(
        {"auth": [{"option_id": "jwt", "label": "JWT"}]}
    ) == {"auth": [AskUserAnswer(option_id="jwt", label="JWT")]}
    assert normalize_ask_user_answers(
        {"auth": {"option_id": "jwt", "label": "JWT"}}
    ) == {"auth": [AskUserAnswer(option_id="jwt", label="JWT")]}
    assert normalize_ask_user_answers(None) == {}


def test_validate_rejects_unknown_question_and_option():
    with pytest.raises(AskUserRequestError):
        validate_ask_user_answers(
            [_question()],
            {"nope": [AskUserAnswer(option_id="jwt", label="JWT")]},
        )
    with pytest.raises(AskUserRequestError):
        validate_ask_user_answers(
            [_question()],
            {"auth": [AskUserAnswer(option_id="oauth", label="OAuth")]},
        )


def test_validate_allows_free_form_and_partial_answers():
    free_form = QuestionInfo(id="why", question="Why?")
    validate_ask_user_answers([free_form], {"why": []})
    validate_ask_user_answers([_question()], {})


def test_validate_rejects_multiple_selections_for_single_select():
    with pytest.raises(AskUserRequestError):
        validate_ask_user_answers(
            [_question()],
            {
                "auth": [
                    AskUserAnswer(option_id="jwt", label="JWT"),
                    AskUserAnswer(option_id="session", label="Session"),
                ]
            },
        )


def test_validate_allows_multiple_selections_when_multi_select():
    question = QuestionInfo(
        id="auth",
        question="Which auth?",
        multi_select=True,
        options=[
            QuestionOption(id="jwt", label="JWT"),
            QuestionOption(id="session", label="Session"),
        ],
    )
    validate_ask_user_answers(
        [question],
        {
            "auth": [
                AskUserAnswer(option_id="jwt", label="JWT"),
                AskUserAnswer(option_id="session", label="Session"),
            ]
        },
    )
