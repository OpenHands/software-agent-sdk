"""Tests for message edits: the event, the tool, and what the agent sees."""

import json
from unittest.mock import patch

import pytest
from litellm import ChatCompletionMessageToolCall
from litellm.types.utils import (
    Choices,
    Function,
    Message as LiteLLMMessage,
    ModelResponse,
)
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.event import (
    ActionEvent,
    Event,
    MessageEditEvent,
    MessageEvent,
    ObservationEvent,
    ReactionEvent,
)
from openhands.sdk.event.message_edit import find_edit, message_text
from openhands.sdk.llm import LLM, Message, MessageToolCall, TextContent
from openhands.sdk.tool import Tool
from openhands.sdk.tool.builtins.finish import FinishAction
from openhands.tools.message_edit import (
    EditMessageAction,
    EditMessageTool,
)
from openhands.tools.message_edit.impl import EditMessageExecutor


QUESTION = "Should I run the lookup on windy?"
STRUCK = "~~Should I run the lookup on windy?~~ [Edit: Running it on windy.]"


def _llm() -> LLM:
    return LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )


def _message(source: str, text: str) -> MessageEvent:
    return MessageEvent(
        source=source,  # type: ignore[arg-type]
        llm_message=Message(
            role="user" if source == "user" else "assistant",
            content=[TextContent(text=text)],
        ),
    )


def _finish(text: str) -> ActionEvent:
    return ActionEvent(
        source="agent",
        thought=[],
        action=FinishAction(message=text),
        tool_name="finish",
        tool_call_id="call-finish",
        tool_call=MessageToolCall(
            id="call-finish",
            name="finish",
            arguments=json.dumps({"message": text}),
            origin="completion",
        ),
        llm_response_id="resp-finish",
    )


# The event and its helpers


def test_an_edit_replaces_text_in_the_agents_message():
    question = _message("agent", f"Looked at the logs. {QUESTION}")

    edit = find_edit([question], QUESTION, STRUCK)

    assert edit.target_event_id == question.id
    assert edit.text == f"Looked at the logs. {STRUCK}"
    assert edit.source == "agent"


def test_the_agents_finish_can_be_edited():
    answer = _finish(f"Done. {QUESTION}")

    edit = find_edit([answer], QUESTION, STRUCK)

    assert edit.target_event_id == answer.id


def test_user_messages_are_not_edited():
    with pytest.raises(ValueError, match="not in any of your messages"):
        find_edit([_message("user", QUESTION)], QUESTION, STRUCK)


def test_old_str_must_be_unique_across_the_agents_messages():
    events = [_message("agent", QUESTION), _message("agent", QUESTION)]

    with pytest.raises(ValueError, match="occurs 2 times"):
        find_edit(events, QUESTION, STRUCK)
    with pytest.raises(ValueError, match="occurs 2 times"):
        find_edit([_message("agent", f"{QUESTION} {QUESTION}")], QUESTION, STRUCK)


def test_empty_or_unchanged_edits_are_refused():
    events = [_message("agent", QUESTION)]

    with pytest.raises(ValueError, match="empty"):
        find_edit(events, "", STRUCK)
    with pytest.raises(ValueError, match="nothing to edit"):
        find_edit(events, QUESTION, QUESTION)


def test_a_second_edit_works_on_the_edited_text():
    question = _message("agent", QUESTION)
    first = find_edit([question], QUESTION, STRUCK)
    events: list[Event] = [question, first]

    second = find_edit(events, "Running it on windy.", "Ran it on windy: 3 hits.")

    assert second.text == (
        "~~Should I run the lookup on windy?~~ [Edit: Ran it on windy: 3 hits.]"
    )
    # The first edit's text is gone once the second replaced it.
    with pytest.raises(ValueError, match="not in any"):
        find_edit([*events, second], "Running it on windy.", "x")
    assert message_text([*events, second], question) == second.text


def test_only_the_agent_edits():
    with pytest.raises(ValueError, match="Only the agent"):
        MessageEditEvent(source="user", target_event_id="x", text="y")


def test_edit_round_trips_as_an_event():
    edit = MessageEditEvent(target_event_id="x", text=STRUCK)

    assert Event.model_validate_json(edit.model_dump_json()) == edit


# The tool


@pytest.fixture
def conversation(tmp_path):
    agent = Agent(llm=_llm(), tools=[Tool(name="edit_message")])
    conv = Conversation(agent=agent, workspace=str(tmp_path), callbacks=[])
    conv.send_message("Please look into the flaky test")
    conv.state.append_event(_message("agent", QUESTION))
    return conv


def _agent_message(conversation) -> MessageEvent:
    return next(
        e
        for e in conversation.state.events
        if isinstance(e, MessageEvent) and e.source == "agent"
    )


def test_executor_returns_the_edit(conversation):
    observation = EditMessageExecutor()(
        EditMessageAction(old_str=QUESTION, new_str=STRUCK), conversation
    )

    assert not observation.is_error
    assert observation.text == f"Edited. The message now reads:\n{STRUCK}"
    (event,) = observation.follow_up_events()
    assert isinstance(event, MessageEditEvent)
    assert event.target_event_id == _agent_message(conversation).id
    assert event.text == STRUCK


def test_a_miss_is_an_error_without_an_edit(conversation):
    observation = EditMessageExecutor()(
        EditMessageAction(old_str="never said this", new_str="x"), conversation
    )

    assert observation.is_error
    assert observation.follow_up_events() == ()


def test_messages_off_the_active_branch_are_not_edited(conversation):
    question = _agent_message(conversation)
    user = next(e for e in conversation.state.events if isinstance(e, MessageEvent))
    conversation.navigate_to(user.id)

    observation = EditMessageExecutor()(
        EditMessageAction(old_str=QUESTION, new_str=STRUCK), conversation
    )

    assert observation.is_error and question.id not in observation.text


def test_edit_tool_is_not_offered_in_agent_settings():
    assert EditMessageTool.user_selectable is False


def _edit_call() -> ModelResponse:
    return ModelResponse(
        id="resp-1",
        choices=[
            Choices(
                index=0,
                message=LiteLLMMessage(
                    role="assistant",
                    content="",
                    tool_calls=[
                        ChatCompletionMessageToolCall(
                            id="call-1",
                            type="function",
                            function=Function(
                                name="edit_message",
                                arguments=json.dumps(
                                    {"old_str": QUESTION, "new_str": STRUCK}
                                ),
                            ),
                        )
                    ],
                ),
                finish_reason="tool_calls",
            )
        ],
        created=0,
        model="test-model",
        object="chat.completion",
    )


def test_agent_records_the_edit_after_the_observation(conversation):
    with patch(
        "openhands.sdk.llm.llm.litellm_completion",
        side_effect=lambda messages, **kw: _edit_call(),
    ):
        conversation.agent.step(conversation, on_event=conversation._on_event)

    observation, edit = list(conversation.state.events)[-2:]
    assert isinstance(observation, ObservationEvent)
    assert isinstance(edit, MessageEditEvent)
    assert edit.target_event_id == _agent_message(conversation).id
    # The message itself is never changed.
    assert _agent_message(conversation).llm_message.content[0].text == QUESTION  # type: ignore[union-attr]


def test_the_agent_sees_its_call_but_not_the_edit_event(conversation):
    with patch(
        "openhands.sdk.llm.llm.litellm_completion",
        side_effect=lambda messages, **kw: _edit_call(),
    ):
        conversation.agent.step(conversation, on_event=conversation._on_event)

    seen: list[list[dict]] = []

    def complete(messages, **kw):
        seen.append(messages)
        return _edit_call()

    with patch("openhands.sdk.llm.llm.litellm_completion", side_effect=complete):
        conversation.agent.step(conversation, on_event=lambda e: None)
    texts = json.dumps(seen[0])

    # The message as first written, the call, and its result; nothing more.
    assert texts.count(json.dumps(STRUCK)[1:-1]) == 2  # call args + observation
    assert QUESTION in texts


def test_a_users_reaction_quotes_the_edited_text(conversation):
    question = _agent_message(conversation)
    conversation.state.append_event(
        MessageEditEvent(target_event_id=question.id, text=STRUCK)
    )

    reaction = conversation.react("+1", question.id)

    assert isinstance(reaction, ReactionEvent)
    assert reaction.target_text_ending == STRUCK
