"""Unit tests for ask_user response dispatch helpers."""

import uuid
from collections.abc import Sequence
from typing import Self

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.agent.response_dispatch import (
    build_ask_user_observation,
    pending_ask_user_request,
)
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event import (
    ActionEvent,
    AskUserAnswer,
    AskUserRequestError,
    AskUserRequestEvent,
    AskUserResponseEvent,
    ObservationEvent,
    QuestionInfo,
    QuestionOption,
)
from openhands.sdk.io import InMemoryFileStore
from openhands.sdk.llm import MessageToolCall
from openhands.sdk.tool import Action, Observation, ToolExecutor, register_tool
from openhands.sdk.tool.builtins.ask_user import AskUserAction
from openhands.sdk.tool.tool import ToolDefinition
from openhands.sdk.workspace import LocalWorkspace


def _state() -> ConversationState:
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    state = ConversationState.create(
        id=uuid.uuid4(),
        agent=Agent(llm=llm),
        workspace=LocalWorkspace(working_dir="/tmp/test"),
        file_store=InMemoryFileStore(),
    )
    return state


def _question() -> QuestionInfo:
    return QuestionInfo(
        id="auth",
        question="Which auth strategy?",
        options=[
            QuestionOption(id="jwt", label="JWT bearer tokens"),
            QuestionOption(id="session", label="Server sessions"),
        ],
    )


def _request() -> AskUserRequestEvent:
    return AskUserRequestEvent(
        request_id="req-1",
        questions=[_question()],
        action_id="action-1",
        tool_call_id="call-1",
    )


def test_pending_ask_user_request_is_none_without_request():
    assert pending_ask_user_request(_state()) is None


def test_pending_ask_user_request_returns_unresolved_request():
    state = _state()
    state.append_event(_request())

    pending = pending_ask_user_request(state)

    assert pending is not None
    assert pending.request_id == "req-1"


def test_pending_ask_user_request_ignores_resolved_request():
    state = _state()
    state.append_event(_request())
    state.append_event(AskUserResponseEvent(request_id="req-1", action="cancel"))

    assert pending_ask_user_request(state) is None


def test_pending_ask_user_request_ignores_mismatched_response():
    state = _state()
    state.append_event(_request())
    state.append_event(AskUserResponseEvent(request_id="other", action="cancel"))

    pending = pending_ask_user_request(state)

    assert pending is not None
    assert pending.request_id == "req-1"


def test_build_observation_accept_lists_selected_labels():
    observation = build_ask_user_observation(
        [_question()],
        "accept",
        {"auth": [AskUserAnswer(option_id="jwt", label="JWT bearer tokens")]},
    )

    assert observation.resolution == "accept"
    assert "JWT bearer tokens" in observation.message


def test_build_observation_accept_includes_every_multi_select_label():
    question = _question().model_copy(update={"multi_select": True})
    observation = build_ask_user_observation(
        [question],
        "accept",
        {
            "auth": [
                AskUserAnswer(option_id="jwt", label="JWT bearer tokens"),
                AskUserAnswer(option_id="session", label="Server sessions"),
            ]
        },
    )

    assert "JWT bearer tokens" in observation.message
    assert "Server sessions" in observation.message
    assert len(observation.answers["auth"]) == 2


def test_build_observation_accept_rejects_unknown_option():
    with pytest.raises(AskUserRequestError):
        build_ask_user_observation(
            [_question()],
            "accept",
            {"auth": [AskUserAnswer(option_id="oauth", label="OAuth")]},
        )


def test_build_observation_accept_rejects_unknown_question():
    with pytest.raises(AskUserRequestError):
        build_ask_user_observation(
            [_question()],
            "accept",
            {"nope": [AskUserAnswer(option_id="jwt", label="JWT")]},
        )


def test_build_observation_accept_allows_partial_answers():
    observation = build_ask_user_observation([_question()], "accept", {})

    assert observation.resolution == "accept"
    assert observation.answers == {}


def _local_conversation_with_state(state: ConversationState):
    from openhands.sdk.agent import Agent as AgentClass
    from openhands.sdk.conversation import Conversation

    conversation = Conversation(agent=AgentClass(llm=LLM(model="test"), tools=[]))
    conversation._ensure_agent_ready()
    conversation._state = state
    return conversation


def test_local_conversation_respond_to_ask_user_appends_response():
    state = _state()
    action_event = _ask_user_action_event()
    state.append_event(action_event)
    state.append_event(
        AskUserRequestEvent(
            request_id="req-1",
            questions=[_question()],
            action_id=action_event.id,
            tool_call_id="call-1",
        )
    )
    conversation = _local_conversation_with_state(state)

    conversation.respond_to_ask_user(
        "req-1",
        "accept",
        {"auth": [AskUserAnswer(option_id="jwt", label="JWT bearer tokens")]},
    )

    responses = [e for e in state.events if isinstance(e, AskUserResponseEvent)]
    assert len(responses) == 1
    assert responses[0].request_id == "req-1"
    assert responses[0].answers["auth"][0].option_id == "jwt"


def test_local_conversation_respond_to_ask_user_accepts_plain_mapping():
    state = _state()
    action_event = _ask_user_action_event()
    state.append_event(action_event)
    state.append_event(
        AskUserRequestEvent(
            request_id="req-1",
            questions=[_question()],
            action_id=action_event.id,
            tool_call_id="call-1",
        )
    )
    conversation = _local_conversation_with_state(state)

    conversation.respond_to_ask_user(
        "req-1", "accept", {"auth": {"option_id": "session", "label": "Sessions"}}
    )

    responses = [e for e in state.events if isinstance(e, AskUserResponseEvent)]
    assert responses[0].answers["auth"][0].option_id == "session"


def test_local_conversation_respond_to_ask_user_rejects_stale_id():
    state = _state()
    action_event = _ask_user_action_event()
    state.append_event(action_event)
    state.append_event(
        AskUserRequestEvent(
            request_id="req-1",
            questions=[_question()],
            action_id=action_event.id,
            tool_call_id="call-1",
        )
    )
    conversation = _local_conversation_with_state(state)

    with pytest.raises(ValueError, match="request_id"):
        conversation.respond_to_ask_user("stale", "cancel")

    assert not any(isinstance(e, AskUserResponseEvent) for e in state.events)


def test_local_conversation_respond_to_ask_user_rejects_unknown_option():
    state = _state()
    action_event = _ask_user_action_event()
    state.append_event(action_event)
    state.append_event(
        AskUserRequestEvent(
            request_id="req-1",
            questions=[_question()],
            action_id=action_event.id,
            tool_call_id="call-1",
        )
    )
    conversation = _local_conversation_with_state(state)

    with pytest.raises(AskUserRequestError):
        conversation.respond_to_ask_user(
            "req-1", "accept", {"auth": {"option_id": "oauth", "label": "OAuth"}}
        )

    assert not any(isinstance(e, AskUserResponseEvent) for e in state.events)


def test_local_conversation_respond_to_ask_user_without_pending_raises():
    state = _state()
    conversation = _local_conversation_with_state(state)

    with pytest.raises(ValueError, match="No pending ask_user request"):
        conversation.respond_to_ask_user("req-1", "cancel")


def test_build_observation_decline_and_cancel():
    decline = build_ask_user_observation([_question()], "decline", {})
    cancel = build_ask_user_observation([_question()], "cancel", {})

    assert decline.resolution == "decline"
    assert cancel.resolution == "cancel"


@pytest.mark.parametrize("action", ["decline", "cancel"])
def test_build_observation_non_accept_ignores_answers(action):
    observation = build_ask_user_observation(
        [_question()],
        action,
        {"auth": [AskUserAnswer(option_id="jwt", label="JWT bearer tokens")]},
    )

    # Only 'accept' carries answers through to the observation.
    assert observation.answers == {}


def _ask_user_action_event(call_id: str = "call-1") -> ActionEvent:
    return ActionEvent(
        source="agent",
        thought=[],
        action=AskUserAction(questions=[_question()]),
        tool_name="ask_user",
        tool_call_id=call_id,
        tool_call=MessageToolCall(
            id=call_id,
            name="ask_user",
            arguments="{}",
            origin="completion",
        ),
        llm_response_id="response-1",
    )


def _run_agent_step(state: ConversationState):
    from pydantic import PrivateAttr

    from openhands.sdk.agent import Agent as AgentClass
    from openhands.sdk.conversation import Conversation
    from openhands.sdk.llm import LLMResponse

    class NoopLLM(LLM):
        _response: LLMResponse = PrivateAttr()

        def completion(self, *, messages, tools=None, **kwargs):  # type: ignore[override]
            raise AssertionError("LLM must not be called while an ask_user is pending")

    merged: list = []

    def on_event(event):
        merged.append(event)
        state.append_event(event)

    conversation = Conversation(agent=AgentClass(llm=NoopLLM(model="test"), tools=[]))
    conversation._ensure_agent_ready()
    conversation._state = state
    AgentClass(llm=NoopLLM(model="test"), tools=[]).step(
        conversation, on_event=on_event
    )
    return merged


def test_unanswered_request_pauses_without_observation():
    state = _state()
    action_event = _ask_user_action_event()
    state.append_event(action_event)
    state.append_event(
        AskUserRequestEvent(
            request_id="req-1",
            questions=[_question()],
            action_id=action_event.id,
            tool_call_id="call-1",
        )
    )

    emitted = _run_agent_step(state)

    assert (
        state.execution_status == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
    )
    assert not any(isinstance(e, ObservationEvent) for e in emitted)


def test_matching_response_emits_resolving_observation():
    state = _state()
    action_event = _ask_user_action_event()
    state.append_event(action_event)
    state.append_event(
        AskUserRequestEvent(
            request_id="req-1",
            questions=[_question()],
            action_id=action_event.id,
            tool_call_id="call-1",
        )
    )
    state.append_event(
        AskUserResponseEvent(
            request_id="req-1",
            action="accept",
            answers={
                "auth": [AskUserAnswer(option_id="jwt", label="JWT bearer tokens")]
            },
        )
    )

    emitted = _run_agent_step(state)

    observations = [e for e in emitted if isinstance(e, ObservationEvent)]
    assert len(observations) == 1
    assert observations[0].action_id == action_event.id
    assert (
        state.execution_status != ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
    )


def _dispatch_ask_user_actions(state: ConversationState, action_events: list):
    emitted: list = []
    agent = Agent(llm=LLM(model="test"), tools=[])
    paused, remaining = agent._handle_ask_user_actions(
        state, action_events, emitted.append
    )
    return paused, remaining, emitted


def test_batch_with_two_ask_user_requests_rejects_the_second():
    state = _state()
    first = _ask_user_action_event("call-1")
    second = _ask_user_action_event("call-2")

    paused, remaining, emitted = _dispatch_ask_user_actions(state, [first, second])

    assert paused is True
    assert remaining == []
    requests = [e for e in emitted if isinstance(e, AskUserRequestEvent)]
    assert len(requests) == 1
    observations = [e for e in emitted if isinstance(e, ObservationEvent)]
    assert len(observations) == 1
    assert observations[0].action_id == second.id


def test_second_request_while_one_is_pending_is_rejected():
    state = _state()
    state.append_event(_ask_user_action_event("call-1"))
    state.append_event(
        AskUserRequestEvent(
            request_id="req-1",
            questions=[_question()],
            action_id="action-1",
            tool_call_id="call-1",
        )
    )
    new_action = _ask_user_action_event("call-2")

    paused, remaining, emitted = _dispatch_ask_user_actions(state, [new_action])

    assert paused is False
    assert remaining == []
    assert not any(isinstance(e, AskUserRequestEvent) for e in emitted)
    observations = [e for e in emitted if isinstance(e, ObservationEvent)]
    assert len(observations) == 1
    assert observations[0].action_id == new_action.id


class _DangerAction(Action):
    pass


class _DangerObservation(Observation):
    executed: bool = False


class _DangerExecutor(ToolExecutor[_DangerAction, _DangerObservation]):
    calls: int = 0

    def __call__(self, action: _DangerAction, conversation=None) -> _DangerObservation:
        _DangerExecutor.calls += 1
        return _DangerObservation(executed=True)


class _DangerTool(ToolDefinition[_DangerAction, _DangerObservation]):
    name = "AskUserRegressionDangerTool"

    @classmethod
    def create(cls, conv_state=None) -> Sequence[Self]:
        return [
            cls(
                description="A tool that must be confirmed",
                action_type=_DangerAction,
                observation_type=_DangerObservation,
                executor=_DangerExecutor(),
            )
        ]


register_tool(_DangerTool.name, _DangerTool)


def _run_mixed_batch_batch_with_confirmation(*, policy):
    """Drive a real run: one assistant message emits [ask_user, danger_tool]."""
    import json

    from openhands.sdk.agent import Agent as AgentClass
    from openhands.sdk.conversation import Conversation
    from openhands.sdk.llm import Message, MessageToolCall, TextContent
    from openhands.sdk.testing import TestLLM
    from openhands.sdk.tool import Tool

    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="asking")],
                tool_calls=[
                    MessageToolCall(
                        id="call-ask",
                        name="ask_user",
                        arguments=json.dumps(
                            {"questions": [{"id": "q", "question": "?"}]}
                        ),
                        origin="completion",
                    ),
                    MessageToolCall(
                        id="call-danger",
                        name=_DangerTool.name,
                        arguments="{}",
                        origin="completion",
                    ),
                ],
            ),
            Message(role="assistant", content=[TextContent(text="done")]),
        ]
    )
    conversation = Conversation(
        agent=AgentClass(
            llm=llm, tools=[Tool(name="ask_user"), Tool(name=_DangerTool.name)]
        ),
        workspace="/tmp/ask_user_regression",
        visualizer=None,
    )
    conversation.set_confirmation_policy(policy)
    conversation.send_message("go")
    conversation.run()

    request = pending_ask_user_request(conversation.state)
    assert request is not None
    conversation.respond_to_ask_user(request.request_id, "accept")
    conversation.run()

    return conversation


def test_actions_batched_with_ask_user_still_require_confirmation():
    """A tool sharing the assistant message with ask_user must not bypass the
    confirmation policy.

    `_handle_tool_calls` returns at the ask_user pause before the confirmation
    check runs, so the sibling action would otherwise be executed unapproved on
    resume.
    """
    from openhands.sdk.conversation.state import ConversationExecutionStatus
    from openhands.sdk.security.confirmation_policy import AlwaysConfirm

    _DangerExecutor.calls = 0
    conversation = _run_mixed_batch_batch_with_confirmation(policy=AlwaysConfirm())

    # The dangerous sibling is gated: it has not run, and the conversation is
    # parked WAITING_FOR_CONFIRMATION for it rather than executing it.
    assert _DangerExecutor.calls == 0
    assert (
        conversation.state.execution_status
        == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
    )

    # An explicit confirmation then lets it run.
    conversation.run()
    assert _DangerExecutor.calls == 1


@pytest.mark.asyncio
async def test_actions_batched_with_ask_user_still_require_confirmation_async():
    """The async twin of the confirmation-bypass regression."""
    import json

    from openhands.sdk.agent import Agent as AgentClass
    from openhands.sdk.conversation import Conversation
    from openhands.sdk.conversation.state import ConversationExecutionStatus
    from openhands.sdk.llm import Message, MessageToolCall, TextContent
    from openhands.sdk.security.confirmation_policy import AlwaysConfirm
    from openhands.sdk.testing import TestLLM
    from openhands.sdk.tool import Tool

    _DangerExecutor.calls = 0
    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="asking")],
                tool_calls=[
                    MessageToolCall(
                        id="call-ask",
                        name="ask_user",
                        arguments=json.dumps(
                            {"questions": [{"id": "q", "question": "?"}]}
                        ),
                        origin="completion",
                    ),
                    MessageToolCall(
                        id="call-danger",
                        name=_DangerTool.name,
                        arguments="{}",
                        origin="completion",
                    ),
                ],
            ),
            Message(role="assistant", content=[TextContent(text="done")]),
        ]
    )
    conversation = Conversation(
        agent=AgentClass(
            llm=llm, tools=[Tool(name="ask_user"), Tool(name=_DangerTool.name)]
        ),
        workspace="/tmp/ask_user_regression_async",
        visualizer=None,
    )
    conversation.set_confirmation_policy(AlwaysConfirm())
    conversation.send_message("go")
    await conversation.arun()

    request = pending_ask_user_request(conversation.state)
    assert request is not None
    conversation.respond_to_ask_user(request.request_id, "accept")
    await conversation.arun()

    assert _DangerExecutor.calls == 0
    assert (
        conversation.state.execution_status
        == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
    )

    await conversation.arun()
    assert _DangerExecutor.calls == 1
