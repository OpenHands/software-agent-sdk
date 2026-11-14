"""Tests for ask_user: the tool, the end of the turn, and how a question is answered."""

import json
import uuid
from collections.abc import Callable
from unittest.mock import patch

import pytest
from litellm import ChatCompletionMessageToolCall
from litellm.types.utils import (
    Choices,
    Function,
    Message as LiteLLMMessage,
    ModelResponse,
)
from pydantic import SecretStr, ValidationError

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent
from openhands.sdk.event.ask_user import (
    ANSWER_HEADER_PREFIX,
    AskUserAnswerEvent,
    find_pending_ask,
)
from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.tool import Tool
from openhands.tools.ask_user import AskUserAction, AskUserOption, AskUserTool


QUESTION = {
    "question": "Ship the fix behind a flag?",
    "options": [
        {"label": "Behind a flag", "consequence": "Off by default."},
        {"label": "On for everyone", "consequence": "Ships to all users."},
    ],
    "recommended": 0,
}


def _llm() -> LLM:
    return LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )


def _tool_calls(*calls: tuple[str, dict]) -> ModelResponse:
    return ModelResponse(
        id=f"resp-{uuid.uuid4().hex[:6]}",
        choices=[
            Choices(
                index=0,
                message=LiteLLMMessage(
                    role="assistant",
                    content="",
                    tool_calls=[
                        ChatCompletionMessageToolCall(
                            id=f"call-{i}-{uuid.uuid4().hex[:6]}",
                            type="function",
                            function=Function(name=name, arguments=json.dumps(args)),
                        )
                        for i, (name, args) in enumerate(calls)
                    ],
                ),
                finish_reason="tool_calls",
            )
        ],
        created=0,
        model="test-model",
        object="chat.completion",
    )


def _text(text: str) -> ModelResponse:
    return ModelResponse(
        id=f"resp-{uuid.uuid4().hex[:6]}",
        choices=[
            Choices(
                index=0,
                message=LiteLLMMessage(role="assistant", content=text),
                finish_reason="stop",
            )
        ],
        created=0,
        model="test-model",
        object="chat.completion",
    )


class _Model:
    """Answers each completion with the next response, and records requests."""

    def __init__(self, *responses: ModelResponse):
        self.responses = list(responses)
        self.requests: list[list[dict]] = []

    def __call__(self, messages, **kw) -> ModelResponse:
        self.requests.append(messages)
        return self.responses.pop(0)


def _run(conversation, model: Callable) -> None:
    with patch("openhands.sdk.llm.llm.litellm_completion", side_effect=model):
        conversation.run()


def _conversation(tmp_path, **kwargs):
    agent = Agent(llm=_llm(), tools=[Tool(name=AskUserTool.name)])
    return Conversation(agent=agent, workspace=str(tmp_path), callbacks=[], **kwargs)


def _asked(tmp_path, **kwargs):
    conversation = _conversation(tmp_path, **kwargs)
    conversation.send_message("Fix the login bug")
    model = _Model(_tool_calls(("ask_user", QUESTION)))
    _run(conversation, model)
    return conversation, model


def _ask_event(conversation) -> ActionEvent:
    return next(
        e
        for e in conversation.state.events
        if isinstance(e, ActionEvent) and e.tool_name == "ask_user"
    )


# The tool


def test_options_are_checked():
    with pytest.raises(ValidationError):
        AskUserAction(question="Which?", options=[AskUserOption(label="Only one")])
    with pytest.raises(ValidationError, match="only 2 options"):
        AskUserAction(
            question="Which?",
            options=[AskUserOption(label="A"), AskUserOption(label="B")],
            recommended=2,
        )


def test_the_tool_is_given_by_the_feature_not_picked_in_settings():
    assert AskUserTool.user_selectable is False
    assert AskUserTool.ends_turn is True


# Asking ends the turn


def test_asking_ends_the_turn_and_leaves_the_question_pending(tmp_path):
    conversation, model = _asked(tmp_path)

    # One call to the model: the run stopped on the question.
    assert len(model.requests) == 1
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED
    pending = find_pending_ask(conversation.state.events)
    assert pending is not None
    assert pending.ask_id == _ask_event(conversation).id
    assert pending.question == QUESTION["question"]
    assert pending.option_labels == ("Behind a flag", "On for everyone")
    assert pending.recommended == 0


def test_other_calls_in_the_same_response_still_run(tmp_path):
    conversation = _conversation(tmp_path)
    conversation.agent.tools.append(Tool(name="think"))  # type: ignore[union-attr]
    conversation.send_message("Fix the login bug")
    _run(
        conversation,
        _Model(_tool_calls(("think", {"thought": "hm"}), ("ask_user", QUESTION))),
    )

    observed = [
        e.tool_name
        for e in conversation.state.events
        if isinstance(e, ObservationEvent)
    ]
    assert observed == ["think", "ask_user"]
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED


def test_an_invalid_question_does_not_end_the_turn(tmp_path):
    conversation = _conversation(tmp_path)
    conversation.send_message("Fix the login bug")
    bad = {**QUESTION, "recommended": 5}
    model = _Model(_tool_calls(("ask_user", bad)), _text("I fixed it."))
    _run(conversation, model)

    # The model saw the error and went on to answer in the same run.
    assert len(model.requests) == 2
    assert find_pending_ask(conversation.state.events) is None


# Answering


def test_a_tap_records_the_answer_and_the_message_the_agent_reads(tmp_path):
    conversation, _ = _asked(tmp_path)
    ask_id = _ask_event(conversation).id

    answer, message = conversation.answer_ask(ask_id, option_index=1, text="today")

    assert isinstance(answer, AskUserAnswerEvent)
    assert (answer.option_index, answer.option_label, answer.text) == (
        1,
        "On for everyone",
        "today",
    )
    assert conversation.state.events[-1].id == answer.id
    assert find_pending_ask(conversation.state.events) is None
    text = message.content[0].text  # type: ignore[union-attr]
    assert text.startswith(ANSWER_HEADER_PREFIX)
    assert QUESTION["question"] in text
    assert text.endswith("On for everyone\ntoday")


def test_the_answer_starts_the_next_turn_with_a_well_formed_history(tmp_path):
    conversation, _ = _asked(tmp_path)
    _, message = conversation.answer_ask(_ask_event(conversation).id, option_index=0)
    conversation.send_message(message)
    model = _Model(_text("Shipping it behind a flag."))
    _run(conversation, model)

    (request,) = model.requests
    roles = [m["role"] for m in request]
    # The tool call is followed by its result, then the person's answer.
    assert roles[-3:] == ["assistant", "tool", "user"]
    assert "Behind a flag" in json.dumps(request[-1])


def test_a_chat_reply_answers_the_question(tmp_path):
    conversation, _ = _asked(tmp_path)
    conversation.send_message("Neither, just revert it")

    assert find_pending_ask(conversation.state.events) is None
    with pytest.raises(ValueError, match="not waiting"):
        conversation.answer_ask(_ask_event(conversation).id, option_index=0)


def test_a_chat_reply_then_runs_cleanly(tmp_path):
    conversation, _ = _asked(tmp_path)
    conversation.send_message("Neither, just revert it")
    model = _Model(_text("Reverted."))
    _run(conversation, model)

    roles = [m["role"] for m in model.requests[0]]
    assert roles[-3:] == ["assistant", "tool", "user"]


def test_a_message_from_another_conversation_does_not_answer(tmp_path):
    conversation, _ = _asked(tmp_path)
    conversation.send_message("Go with the flag", sender="conversation:abc")

    assert find_pending_ask(conversation.state.events) is not None


def test_an_answer_is_refused_for_another_question_or_option(tmp_path):
    conversation, _ = _asked(tmp_path)
    ask_id = _ask_event(conversation).id

    with pytest.raises(ValueError, match="not waiting"):
        conversation.answer_ask("not-the-question", option_index=0)
    with pytest.raises(ValueError, match="no option 2"):
        conversation.answer_ask(ask_id, option_index=2)
    with pytest.raises(ValueError, match="option, gives text"):
        conversation.answer_ask(ask_id, text="  ")
    # Nothing was recorded by the refusals.
    assert find_pending_ask(conversation.state.events) is not None


def test_a_second_tap_is_refused(tmp_path):
    conversation, _ = _asked(tmp_path)
    ask_id = _ask_event(conversation).id
    conversation.answer_ask(ask_id, option_index=0)

    with pytest.raises(ValueError, match="not waiting"):
        conversation.answer_ask(ask_id, option_index=1)


def test_the_question_survives_a_restart(tmp_path):
    persistence = tmp_path / "persist"
    conversation_id = uuid.uuid4()
    conversation, _ = _asked(
        tmp_path, persistence_dir=str(persistence), conversation_id=conversation_id
    )
    ask_id = _ask_event(conversation).id
    del conversation

    reopened = _conversation(
        tmp_path, persistence_dir=str(persistence), conversation_id=conversation_id
    )
    pending = find_pending_ask(reopened.state.events)
    assert pending is not None and pending.ask_id == ask_id

    reopened.answer_ask(ask_id, option_index=0)
    assert find_pending_ask(reopened.state.events) is None


def test_the_answer_event_round_trips():
    event = AskUserAnswerEvent(ask_id="a", option_index=0, option_label="Yes")
    assert AskUserAnswerEvent.model_validate_json(event.model_dump_json()) == event
    with pytest.raises(ValidationError):
        AskUserAnswerEvent(ask_id="a")


def test_the_person_s_own_message_is_what_closes_it():
    user = MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text="hi")]),
    )
    assert find_pending_ask([user]) is None
