"""Tests for emoji reactions: the event, the tools and LocalConversation.react."""

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
    REACTION_EMOJI,
    ActionEvent,
    Event,
    MessageEvent,
    ObservationEvent,
    ReactionEvent,
)
from openhands.sdk.event.reaction import (
    CLIPPED_MESSAGE_START,
    REACTION_TEXT_ENDING_MAX,
    check_reaction_emoji,
    find_reaction_target,
)
from openhands.sdk.llm import LLM, Message, MessageToolCall, TextContent
from openhands.sdk.tool import Tool
from openhands.sdk.tool.builtins.finish import FinishAction
from openhands.tools.reaction import (
    ReactAction,
    ReactionObservation,
    ReactTool,
    UnreactAction,
    UnreactTool,
)
from openhands.tools.reaction.impl import ReactionExecutor


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


# The event and its helpers


def test_emoji_are_checked():
    assert check_reaction_emoji("eyes") == "eyes"
    with pytest.raises(ValueError, match="use one of: eyes"):
        check_reaction_emoji("rocket")
    assert "+1" in REACTION_EMOJI


def test_default_target_is_the_latest_message_from_the_other_side():
    first, answer, second = (
        _message("user", "a"),
        _message("agent", "b"),
        _message("user", "c"),
    )
    events = [first, answer, second]

    assert find_reaction_target(events, None, source="agent") is second
    assert find_reaction_target(events, None, source="user") is answer
    assert find_reaction_target(events, first.id, source="agent") is first


def test_the_agents_finish_counts_as_its_answer():
    question = _message("user", "fix it")
    finish = ActionEvent(
        source="agent",
        thought=[TextContent(text="")],
        action=FinishAction(message="Fixed."),
        tool_name="finish",
        tool_call_id="finish-1",
        tool_call=MessageToolCall(
            id="finish-1",
            name="finish",
            arguments='{"message": "Fixed."}',
            origin="completion",
        ),
        llm_response_id="resp-1",
    )

    assert find_reaction_target([question, finish], None, source="user") is finish
    assert find_reaction_target([question, finish], finish.id, source="user") is finish


def test_target_must_be_a_message():
    other = ReactionEvent(target_event_id="x", emoji="eyes")
    events: list[Event] = [_message("agent", "hi"), other]

    with pytest.raises(ValueError, match="not a message"):
        find_reaction_target(events, other.id, source="user")
    with pytest.raises(ValueError, match="No message"):
        find_reaction_target(events, "missing", source="user")
    with pytest.raises(ValueError, match="no user message"):
        find_reaction_target(events, None, source="agent")


def test_reaction_round_trips_as_an_event():
    event = ReactionEvent(source="agent", target_event_id="m1", emoji="+1")
    loaded = Event.model_validate(json.loads(event.model_dump_json()))
    assert isinstance(loaded, ReactionEvent)
    assert (loaded.emoji, loaded.removed, loaded.source) == ("+1", False, "agent")


def test_only_the_agent_and_the_person_react():
    with pytest.raises(ValueError, match="agent or the user"):
        ReactionEvent(source="environment", target_event_id="m1", emoji="eyes")


def test_observations_have_no_follow_up_events_by_default():
    assert ReactionObservation.from_text("x", is_error=True).follow_up_events() == ()


# The tools


@pytest.fixture
def conversation(tmp_path):
    agent = Agent(llm=_llm(), tools=[Tool(name="react"), Tool(name="unreact")])
    conv = Conversation(agent=agent, workspace=str(tmp_path), callbacks=[])
    conv.send_message("Please look into the flaky test")
    return conv


def _user_message(conversation) -> MessageEvent:
    return next(
        e
        for e in conversation.state.events
        if isinstance(e, MessageEvent) and e.source == "user"
    )


def test_executor_targets_the_latest_user_message(conversation):
    observation = ReactionExecutor()(ReactAction(emoji="eyes"), conversation)

    assert not observation.is_error
    (event,) = observation.follow_up_events()
    assert isinstance(event, ReactionEvent)
    assert event.target_event_id == _user_message(conversation).id
    assert (event.source, event.emoji, event.removed) == ("agent", "eyes", False)


def test_unreact_records_a_removal(conversation):
    target = _user_message(conversation)

    observation = ReactionExecutor()(
        UnreactAction(emoji="eyes", message_id=target.id), conversation
    )

    (event,) = observation.follow_up_events()
    assert isinstance(event, ReactionEvent)
    assert event.removed and event.target_event_id == target.id


def test_unknown_message_is_an_error_without_a_reaction(conversation):
    observation = ReactionExecutor()(
        ReactAction(emoji="eyes", message_id="nope"), conversation
    )

    assert observation.is_error
    assert observation.follow_up_events() == ()


def _react_call() -> ModelResponse:
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
                                name="react", arguments='{"emoji": "eyes"}'
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


def test_agent_records_the_reaction_after_the_observation(conversation):
    emitted: list[Event] = []

    with patch(
        "openhands.sdk.llm.llm.litellm_completion",
        side_effect=lambda messages, **kw: _react_call(),
    ):
        conversation.agent.step(conversation, on_event=emitted.append)

    kinds = [type(e).__name__ for e in emitted]
    assert kinds[-2:] == ["ObservationEvent", "ReactionEvent"]
    observation, reaction = emitted[-2:]
    assert isinstance(observation, ObservationEvent)
    assert isinstance(reaction, ReactionEvent)
    assert reaction.target_event_id == _user_message(conversation).id
    # The LLM sees the short observation, never the reaction event.
    assert "Reacted :eyes:" in observation.to_llm_message().content[0].text  # type: ignore[union-attr]


# LocalConversation.react, the person's side


def test_person_reacts_to_the_agents_latest_message(conversation):
    with pytest.raises(ValueError, match="no agent message"):
        conversation.react("+1")

    conversation.state.append_event(_message("agent", "Fixed it."))
    event = conversation.react("+1")
    removal = conversation.react("+1", event.target_event_id, removed=True)

    assert event.source == "user" and not event.removed
    assert removal.removed and removal.target_event_id == event.target_event_id
    saved = [e for e in conversation.state.events if isinstance(e, ReactionEvent)]
    assert [e.id for e in saved] == [event.id, removal.id]


def test_person_cannot_use_an_unknown_emoji(conversation):
    with pytest.raises(ValueError, match="Unknown emoji"):
        conversation.react("rocket")


def test_messages_off_the_active_branch_are_not_targets(conversation):
    question = _user_message(conversation)
    answer = _message("agent", "Old answer.")
    conversation.state.append_event(answer)

    conversation.navigate_to(question.id)

    with pytest.raises(ValueError, match="no agent message"):
        conversation.react("+1")
    with pytest.raises(ValueError, match="No message"):
        conversation.react("+1", answer.id)


def test_execute_tool_returns_the_reaction_without_recording_it(conversation):
    observation = conversation.execute_tool("react", ReactAction(emoji="tada"))

    assert not observation.is_error
    (follow_up,) = observation.follow_up_events()
    assert isinstance(follow_up, ReactionEvent) and follow_up.emoji == "tada"
    assert not [e for e in conversation.state.events if isinstance(e, ReactionEvent)]


def test_reaction_tools_are_not_offered_in_agent_settings():
    assert ReactTool.user_selectable is False
    assert UnreactTool.user_selectable is False


# What the agent sees of reactions


def _messages_on_next_step(conversation) -> list[dict]:
    """The messages the agent sends the LLM on its next step."""
    seen: list[list[dict]] = []

    def complete(messages, **kw):
        seen.append(messages)
        return _react_call()

    with patch("openhands.sdk.llm.llm.litellm_completion", side_effect=complete):
        conversation.agent.step(conversation, on_event=lambda e: None)
    return seen[0]


def _texts(messages: list[dict]) -> list[str]:
    texts = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.extend(c.get("text", "") for c in content if isinstance(c, dict))
    return texts


def test_the_agent_sees_the_persons_reaction_on_its_next_turn(conversation):
    question = "Shall I delete the generated fixtures?"
    conversation.state.append_event(_message("agent", question))
    status = conversation.state.execution_status

    conversation.react("+1")

    # Reacting saves the event and starts nothing.
    assert conversation.state.execution_status == status
    texts = _texts(_messages_on_next_step(conversation))
    assert f"[The person reacted 👍 to this message:]\n{question}" in texts


def test_a_reaction_during_a_pending_tool_call_waits_for_its_result(conversation):
    """LLM APIs reject anything between a tool call and its result."""
    conversation.state.append_event(_message("agent", "Shall I run it?"))
    emitted: list[Event] = []
    with patch(
        "openhands.sdk.llm.llm.litellm_completion",
        side_effect=lambda messages, **kw: _react_call(),
    ):
        conversation.agent.step(conversation, on_event=emitted.append)
    call = next(e for e in emitted if isinstance(e, ActionEvent))
    result = next(e for e in emitted if isinstance(e, ObservationEvent))

    def kinds(events) -> list[str]:
        return [type(e).__name__ for e in events]

    # The call waits (a confirmation, a pause); the person reacts meanwhile.
    conversation._on_event(call)
    held = conversation.react("heart")
    conversation.react("heart", removed=True)
    assert kinds(conversation.state.events)[-1] == "ActionEvent"

    # The result arrives: the held reactions follow it, in order.
    conversation._on_event(result)

    assert kinds(conversation.state.events)[-4:] == [
        "ActionEvent",
        "ObservationEvent",
        "ReactionEvent",
        "ReactionEvent",
    ]
    first, second = conversation.state.events[-2:]
    assert (first.id, first.removed, second.removed) == (held.id, False, True)
    assert first.timestamp >= conversation.state.events[-3].timestamp
    assert (
        kinds(conversation.state.view.events)[-4:]
        == kinds(conversation.state.events)[-4:]
    )
    roles = [m["role"] for m in _messages_on_next_step(conversation)]
    assert roles[-3:] == ["assistant", "tool", "user"]

    # Nothing is open any more: the next reaction is an event at once.
    conversation.react("tada")
    assert kinds(conversation.state.events)[-1] == "ReactionEvent"


def test_a_long_message_is_clipped_to_its_ending(conversation):
    long_answer = "Start of a long plan. " + "step " * 200 + "Shall I go ahead?"
    conversation.state.append_event(_message("agent", long_answer))

    event = conversation.react("+1")

    assert event.target_text_clipped
    assert event.target_text_ending == long_answer[-REACTION_TEXT_ENDING_MAX:]
    (content,) = event.to_llm_message().content
    assert isinstance(content, TextContent)
    assert content.text == (
        "[The person reacted 👍 to this message:]\n"
        f"{CLIPPED_MESSAGE_START}\n{long_answer[-REACTION_TEXT_ENDING_MAX:]}"
    )


def test_removal_and_older_reactions_read_as_one_line():
    removal = ReactionEvent(
        target_event_id="m1",
        emoji="heart",
        removed=True,
        target_text_ending="Done.",
    )
    older = ReactionEvent(target_event_id="m1", emoji="tada")

    assert removal.to_llm_message().content[0].text == (  # type: ignore[union-attr]
        "[The person took back the ❤️ reaction on this message:]\nDone."
    )
    assert older.to_llm_message().content[0].text == (  # type: ignore[union-attr]
        "[The person reacted 🎉 to a message.]"
    )


def test_the_agents_own_reaction_is_not_repeated_to_it(conversation):
    with patch(
        "openhands.sdk.llm.llm.litellm_completion",
        side_effect=lambda messages, **kw: _react_call(),
    ):
        conversation.agent.step(conversation, on_event=conversation._on_event)
    assert any(isinstance(e, ReactionEvent) for e in conversation.state.events)

    texts = _texts(_messages_on_next_step(conversation))

    assert not any("You reacted" in text for text in texts)
    assert any("Reacted :eyes:" in text for text in texts)
