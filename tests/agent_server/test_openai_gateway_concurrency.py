"""Overlapping chat completions on one continued conversation."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from openhands.agent_server.openai import service
from openhands.agent_server.openai.models import OpenAIChatCompletionRequest
from openhands.sdk.conversation.state import ConversationExecutionStatus


class _FakeEventService:
    def __init__(self, status: ConversationExecutionStatus) -> None:
        self.status = status
        self.sent: list[object] = []

    async def get_state(self):
        stats = MagicMock()
        stats.get_combined_metrics.return_value.accumulated_token_usage = None
        return SimpleNamespace(execution_status=self.status, events=[], stats=stats)

    async def send_message(self, message, run: bool = False) -> None:
        self.sent.append(message)
        self.status = ConversationExecutionStatus.RUNNING

    async def get_agent_final_response(self) -> str:
        return f"answer to turn {len(self.sent)}"


def _request(text: str) -> OpenAIChatCompletionRequest:
    return OpenAIChatCompletionRequest.model_validate(
        {"model": "openhands_fast", "messages": [{"role": "user", "content": text}]}
    )


@pytest.fixture
def gateway(monkeypatch):
    def make(status: ConversationExecutionStatus):
        event_service = _FakeEventService(status)
        conversation_service = MagicMock()

        async def get_event_service(_conversation_id):
            return event_service

        conversation_service.get_event_service = get_event_service
        run_may_finish = asyncio.Event()

        async def wait_for_completion(svc, *, min_event_count):
            await run_may_finish.wait()
            svc.status = ConversationExecutionStatus.FINISHED
            return svc.status

        monkeypatch.setattr(service, "_conversation_request", lambda **_: MagicMock())
        monkeypatch.setattr(
            service, "_wait_for_reused_conversation_completion", wait_for_completion
        )
        return event_service, conversation_service, run_may_finish

    return make


def _chat(conversation_service, conversation_id, text: str):
    # Bounded, so a request that queues behind a turn fails instead of hanging.
    return asyncio.wait_for(
        service.run_chat_completion(
            request=_request(text),
            config=MagicMock(),
            conversation_service=conversation_service,
            reusable_conversation_id=conversation_id,
        ),
        timeout=5,
    )


async def test_overlapping_turn_is_refused_and_first_gets_its_own_answer(gateway):
    event_service, conversation_service, run_may_finish = gateway(
        ConversationExecutionStatus.FINISHED
    )
    conversation_id = uuid4()

    first = asyncio.create_task(_chat(conversation_service, conversation_id, "X"))
    while not event_service.sent:
        await asyncio.sleep(0)
    # The run X started has finished, but X's request has not seen it yet.
    event_service.status = ConversationExecutionStatus.FINISHED

    with pytest.raises(HTTPException) as exc_info:
        await _chat(conversation_service, conversation_id, "Y")
    assert exc_info.value.status_code == 409
    assert len(event_service.sent) == 1  # Y's message was not saved

    run_may_finish.set()
    result = await first
    assert result.response.choices[0].message.content == "answer to turn 1"

    # Once X has its answer, the conversation takes the next turn.
    second = await _chat(conversation_service, conversation_id, "Y")
    assert second.response.choices[0].message.content == "answer to turn 2"


async def test_turn_on_a_conversation_running_elsewhere_is_refused(gateway):
    event_service, conversation_service, _ = gateway(
        ConversationExecutionStatus.RUNNING
    )

    with pytest.raises(HTTPException) as exc_info:
        await _chat(conversation_service, uuid4(), "Y")

    assert exc_info.value.status_code == 409
    assert event_service.sent == []
