"""Pause/resume behavior for the ``ask_user`` tool.

Covers the run pausing at ``WAITING_FOR_CONFIRMATION`` when the agent calls
``ask_user``, the user's answer being consumed as the tool observation, and the
run resuming to completion.
"""

import json
from unittest.mock import MagicMock, patch

from litellm import ChatCompletionMessageToolCall
from litellm.types.utils import (
    Choices,
    Function,
    Message as LiteLLMMessage,
    ModelResponse,
)
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation, LocalConversation
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event import MessageEvent, ObservationEvent
from openhands.sdk.event.llm_convertible.message import TextContent
from openhands.sdk.llm import LLM
from openhands.sdk.tool import Tool
from openhands.sdk.tool.builtins import AskUserTool
from openhands.sdk.tool.builtins.ask_user import AskUserAnswerAction, AskUserObservation


def _ask_observations(conversation: LocalConversation) -> list[AskUserObservation]:
    return [
        e.observation
        for e in conversation.state.events
        if isinstance(e, ObservationEvent)
        and isinstance(e.observation, AskUserObservation)
    ]


def _message_texts(conversation: LocalConversation, source: str) -> list[str]:
    texts: list[str] = []
    for event in conversation.state.events:
        if isinstance(event, MessageEvent) and event.source == source:
            texts.extend(
                c.text for c in event.llm_message.content if isinstance(c, TextContent)
            )
    return texts


ASK_ARGS = json.dumps(
    {
        "questions": [
            {
                "question": "Which auth?",
                "header": "auth",
                "options": [
                    {"id": "jwt", "label": "JWT", "description": "token"},
                    {"id": "session", "label": "Session", "description": "cookie"},
                ],
                "multi_select": False,
            }
        ]
    }
)


def _response(response_id: str, content: str, tool_calls=None) -> ModelResponse:
    return ModelResponse(
        id=response_id,
        choices=[
            Choices(
                message=LiteLLMMessage(
                    role="assistant", content=content, tool_calls=tool_calls
                )
            )
        ],
        created=0,
        model="test-model",
        object="chat.completion",
    )


def _tool_call(
    call_id: str, name: str, arguments: str
) -> ChatCompletionMessageToolCall:
    return ChatCompletionMessageToolCall(
        id=call_id, type="function", function=Function(name=name, arguments=arguments)
    )


class TestAskUserPauseResume:
    def setup_method(self):
        llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="t")
        self.agent = Agent(llm=llm, tools=[Tool(name=AskUserTool.name)])
        self.conversation: LocalConversation = Conversation(agent=self.agent)

    def _ask_once(self, call_id: str = "ask_1") -> MagicMock:
        return MagicMock(
            return_value=_response(
                "resp_ask",
                "I need to ask",
                [_tool_call(call_id, "ask_user", ASK_ARGS)],
            )
        )

    def _finish(self) -> MagicMock:
        return MagicMock(
            return_value=_response(
                "resp_finish",
                "done",
                [_tool_call("finish_1", "finish", '{"message": "done"}')],
            )
        )

    def _run_to_pause(self) -> str:
        with patch(
            "openhands.sdk.llm.llm.litellm_completion",
            return_value=self._ask_once().return_value,
        ):
            self.conversation.send_message("add auth")
            self.conversation.run()
        assert (
            self.conversation.state.execution_status
            == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
        )
        pending = ConversationState.get_unmatched_actions(
            self.conversation.state.events
        )
        assert len(pending) == 1
        assert pending[0].tool_name == AskUserTool.name
        return pending[0].id

    def _answer(self, request_id: str, **payload) -> None:
        body = {"request_id": request_id, **payload}
        self.conversation.send_message(json.dumps(body))

    def test_call_pauses_and_answer_resolves(self):
        request_id = self._run_to_pause()

        self._answer(
            request_id,
            action="accept",
            answers={"auth": [{"option_id": "jwt"}]},
        )

        # The answer resolved the pending request without adding a user turn.
        assert (
            ConversationState.get_unmatched_actions(self.conversation.state.events)
            == []
        )
        observations = _ask_observations(self.conversation)
        assert len(observations) == 1
        observation = observations[0]
        assert observation.request_id == request_id
        assert observation.answers[0].selected[0].option_id == "jwt"

        assert _message_texts(self.conversation, "user") == ["add auth"]

        with patch(
            "openhands.sdk.llm.llm.litellm_completion",
            return_value=self._finish().return_value,
        ):
            self.conversation.run()
        assert (
            self.conversation.state.execution_status
            == ConversationExecutionStatus.FINISHED
        )

    def test_decline_resolves_without_answers(self):
        request_id = self._run_to_pause()
        self._answer(request_id, action="decline")
        observations = _ask_observations(self.conversation)
        assert len(observations) == 1
        assert observations[0].action is AskUserAnswerAction.DECLINE
        assert observations[0].answers == []

    def test_invalid_answer_keeps_request_pending(self):
        request_id = self._run_to_pause()
        self._answer(
            request_id,
            action="accept",
            answers={"auth": [{"option_id": "does-not-exist"}]},
        )
        # The bad answer produced a corrective notice, not an observation, and
        # the request stays pending for a retry.
        pending = ConversationState.get_unmatched_actions(
            self.conversation.state.events
        )
        assert len(pending) == 1
        assert _ask_observations(self.conversation) == []
        assert any(
            "could not be used" in text
            for text in _message_texts(self.conversation, "environment")
        )

        # A valid retry resolves it.
        self._answer(
            request_id, action="accept", answers={"auth": [{"option_id": "session"}]}
        )
        assert (
            ConversationState.get_unmatched_actions(self.conversation.state.events)
            == []
        )

    def test_non_answer_message_is_a_normal_turn(self):
        self._run_to_pause()
        self.conversation.send_message("never mind, keep going")
        assert _message_texts(self.conversation, "user") == [
            "add auth",
            "never mind, keep going",
        ]
        # Still pending: the plain message did not resolve the request.
        assert (
            len(ConversationState.get_unmatched_actions(self.conversation.state.events))
            == 1
        )
