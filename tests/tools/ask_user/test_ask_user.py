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
from openhands.sdk.conversation.goal import (
    GoalAsked,
    GoalController,
    GoalOutcome,
    run_goal,
)
from openhands.sdk.conversation.goal.judge import GoalVerdict
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.critic.base import IterativeRefinementConfig
from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent
from openhands.sdk.event.ask_user import (
    ANSWER_HEADER_PREFIX,
    AskUserAnswerEvent,
    find_pending_ask,
)
from openhands.sdk.hooks import HookConfig, HookDefinition, HookMatcher
from openhands.sdk.llm import LLM, Message, MessageToolCall, TextContent
from openhands.sdk.security.confirmation_policy import AlwaysConfirm
from openhands.sdk.tool import Tool
from openhands.tools.ask_user import (
    AskUserAction,
    AskUserObservation,
    AskUserOption,
    AskUserTool,
)
from tests.command_utils import python_command
from tests.sdk.agent.test_iterative_refinement import MockCritic


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


def _conversation(tmp_path, *, critic=None, **kwargs):
    agent = Agent(llm=_llm(), tools=[Tool(name=AskUserTool.name)], critic=critic)
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


@pytest.mark.parametrize(
    "invalid_fields",
    [
        {"recommended": 5},
        {"options": [{"label": " \t"}, {"label": "Ship"}]},
    ],
    ids=["missing-recommendation", "blank-label"],
)
def test_an_invalid_question_does_not_end_the_turn(tmp_path, invalid_fields):
    conversation = _conversation(tmp_path)
    conversation.send_message("Fix the login bug")
    bad = {**QUESTION, **invalid_fields}
    model = _Model(_tool_calls(("ask_user", bad)), _text("I fixed it."))
    _run(conversation, model)

    # The model saw the error and went on to answer in the same run.
    assert len(model.requests) == 2
    assert find_pending_ask(conversation.state.events) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
async def test_stop_hooks_wait_for_an_answer_but_still_check_completion(
    tmp_path, use_async
):
    marker = tmp_path / "stop-hook-ran"
    command = python_command(
        "from pathlib import Path; import sys; "
        f"marker = Path({str(marker)!r}); existed = marker.exists(); "
        "marker.touch(); print('Keep working', file=sys.stderr); "
        "sys.exit(0 if existed else 2)"
    )
    conversation = _conversation(
        tmp_path,
        hook_config=HookConfig(
            stop=[HookMatcher(hooks=[HookDefinition(command=command)])]
        ),
    )
    model = _Model(_tool_calls(("ask_user", QUESTION)), _text("Premature completion"))
    try:
        conversation.send_message("Fix the login bug")
        with (
            patch("openhands.sdk.llm.llm.litellm_completion", side_effect=model),
            patch("openhands.sdk.llm.llm.litellm_acompletion", side_effect=model),
        ):
            if use_async:
                await conversation.arun()
            else:
                conversation.run()
        assert find_pending_ask(conversation.state.events) is not None
        assert len(model.requests) == 1
        assert not marker.exists()

        conversation.send_message("Use the flag")
        completion = _Model(_text("Completed"), _text("Checked and completed"))
        with (
            patch("openhands.sdk.llm.llm.litellm_completion", side_effect=completion),
            patch("openhands.sdk.llm.llm.litellm_acompletion", side_effect=completion),
        ):
            if use_async:
                await conversation.arun()
            else:
                conversation.run()
        assert find_pending_ask(conversation.state.events) is None
        assert len(completion.requests) == 2
        assert marker.exists()
    finally:
        conversation.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
async def test_a_question_takes_precedence_over_finish_refinement(tmp_path, use_async):
    conversation = _conversation(
        tmp_path,
        critic=MockCritic(
            iterative_refinement=IterativeRefinementConfig(max_iterations=1)
        ),
    )
    model = _Model(
        _tool_calls(("ask_user", QUESTION), ("finish", {"message": "Done"})),
        _text("Premature refinement"),
    )
    try:
        conversation.send_message("Fix the login bug")
        with (
            patch("openhands.sdk.llm.llm.litellm_completion", side_effect=model),
            patch("openhands.sdk.llm.llm.litellm_acompletion", side_effect=model),
        ):
            if use_async:
                await conversation.arun()
            else:
                conversation.run()
        assert find_pending_ask(conversation.state.events) is not None
        assert len(model.requests) == 1
    finally:
        conversation.close()


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
    # The message the agent reads is what closes the question.
    assert find_pending_ask(conversation.state.events) is not None
    conversation.send_message(message)
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
    # The tool call is followed by its result, then the user's answer.
    assert roles[-3:] == ["assistant", "tool", "user"]
    assert "Behind a flag" in json.dumps(request[-1])


def test_equal_labels_preserve_which_option_the_user_selected(tmp_path):
    conversation = _conversation(tmp_path)
    question = {
        "question": "Choose an environment?",
        "options": [
            {"label": "Default", "consequence": "Use production"},
            {"label": "Default", "consequence": "Use staging"},
        ],
    }
    try:
        conversation.send_message("Choose an environment")
        _run(conversation, _Model(_tool_calls(("ask_user", question))))
        ask_id = _ask_event(conversation).id
        _, production = conversation.answer_ask(ask_id, option_index=0)
        _, staging = conversation.answer_ask(ask_id, option_index=1)
        assert production.content == [
            TextContent(
                text='[Answer to your question: "Choose an environment?"]\n'
                "Option 1: Default"
            )
        ]
        assert staging.content == [
            TextContent(
                text='[Answer to your question: "Choose an environment?"]\n'
                "Option 2: Default"
            )
        ]
    finally:
        conversation.close()


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
    _, message = conversation.answer_ask(ask_id, option_index=0)
    conversation.send_message(message)

    with pytest.raises(ValueError, match="not waiting"):
        conversation.answer_ask(ask_id, option_index=1)


def test_a_tap_whose_message_was_lost_can_be_sent_again(tmp_path):
    # The process died after saving the answer event, before the message.
    conversation, _ = _asked(tmp_path)
    ask_id = _ask_event(conversation).id
    conversation.answer_ask(ask_id, option_index=0)

    assert find_pending_ask(conversation.state.events) is not None
    _, message = conversation.answer_ask(ask_id, option_index=1)
    conversation.send_message(message)
    assert find_pending_ask(conversation.state.events) is None


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

    _, message = reopened.answer_ask(ask_id, option_index=0)
    reopened.send_message(message)
    assert find_pending_ask(reopened.state.events) is None


def test_the_answer_event_round_trips():
    event = AskUserAnswerEvent(ask_id="a", option_index=0, option_label="Yes")
    assert AskUserAnswerEvent.model_validate_json(event.model_dump_json()) == event
    with pytest.raises(ValidationError):
        AskUserAnswerEvent(ask_id="a")


def test_the_user_s_own_message_is_what_closes_it():
    user = MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text="hi")]),
    )
    assert find_pending_ask([user]) is None


def test_an_out_of_range_recommendation_is_dropped():
    action = ActionEvent(
        source="agent",
        thought=[TextContent(text="")],
        action=AskUserAction.model_construct(
            question="Q?",
            options=[AskUserOption(label="A"), AskUserOption(label="B")],
            recommended=7,
        ),
        tool_name="ask_user",
        tool_call_id="c",
        tool_call=MessageToolCall(
            id="c", name="ask_user", arguments="{}", origin="completion"
        ),
        llm_response_id="r",
    )
    shown = ObservationEvent(
        source="environment",
        observation=AskUserObservation.from_text("shown"),
        action_id=action.id,
        tool_name="ask_user",
        tool_call_id="c",
    )
    pending = find_pending_ask([action, shown])
    assert pending is not None and pending.recommended is None


# One question per response, and no confirmation for it


def test_only_the_first_question_in_a_response_is_asked(tmp_path):
    conversation = _conversation(tmp_path)
    conversation.send_message("Fix the login bug")
    second = {**QUESTION, "question": "Which region?"}
    _run(
        conversation,
        _Model(_tool_calls(("ask_user", QUESTION), ("ask_user", second))),
    )

    results = [
        e.observation.is_error
        for e in conversation.state.events
        if isinstance(e, ObservationEvent)
    ]
    assert results == [False, True]
    pending = find_pending_ask(conversation.state.events)
    assert pending is not None and pending.question == QUESTION["question"]
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED


def test_asking_needs_no_confirmation_even_when_every_action_does(tmp_path):
    conversation = _conversation(tmp_path)
    conversation.set_confirmation_policy(AlwaysConfirm())
    conversation.send_message("Fix the login bug")
    _run(conversation, _Model(_tool_calls(("ask_user", QUESTION))))

    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED
    assert find_pending_ask(conversation.state.events) is not None


# /goal


def test_a_goal_stops_on_a_question_without_judging(tmp_path):
    conversation, _ = _asked(tmp_path)
    controller = GoalController("Fix the login bug", _llm())

    with patch("openhands.sdk.conversation.goal.controller.judge_goal") as judge:
        step = controller.on_run_finished(list(conversation.state.events))

    judge.assert_not_called()
    assert isinstance(step, GoalAsked)
    assert step.ask_id == _ask_event(conversation).id
    assert controller.iteration == 0

    outcome = run_goal(conversation, "Fix the login bug", _llm())
    assert isinstance(outcome, GoalAsked)
    assert outcome.ask_id == step.ask_id
    assert find_pending_ask(conversation.state.events) is not None


def test_goal_ignores_question_on_abandoned_branch(tmp_path):
    conversation, _ = _asked(tmp_path)
    initial = next(
        event
        for event in conversation.state.events
        if isinstance(event, MessageEvent) and event.source == "user"
    )
    conversation.navigate_to(initial.id)
    assert find_pending_ask(conversation.state.active_branch()) is None

    with (
        patch(
            "openhands.sdk.llm.llm.litellm_completion",
            return_value=_text("Completed the new objective"),
        ),
        patch(
            "openhands.sdk.conversation.goal.controller.judge_goal",
            return_value=GoalVerdict(score=1.0, complete=True, missing=""),
        ),
    ):
        outcome = run_goal(conversation, "Complete the new objective", _llm())

    assert isinstance(outcome, GoalOutcome)
    assert outcome.status == "complete"
