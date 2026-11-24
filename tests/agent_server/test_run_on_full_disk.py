"""An agent-server run whose storage fills up mid-step (#7102)."""

import errno
from typing import Any

import pytest
from pydantic import PrivateAttr

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.pub_sub import Subscriber
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import Event
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.event.conversation_state import (
    FULL_STATE_KEY,
    ConversationStateUpdateEvent,
)
from openhands.sdk.io import LocalFileStore
from openhands.sdk.llm import LLMResponse, Message, TextContent
from tests.agent_server.stress.scripts import (
    SlowTestLLM,
    start_conversation_with_test_llm,
    text_message,
)


DISK = {"full": False}


@pytest.fixture
def full_disk(monkeypatch):
    """Fail every conversation-state write with ENOSPC while DISK["full"]."""
    original_write = LocalFileStore.write

    def write(self: LocalFileStore, path: str, contents: str | bytes) -> None:
        if DISK["full"]:
            raise OSError(errno.ENOSPC, "No space left on device")
        original_write(self, path, contents)

    monkeypatch.setattr(LocalFileStore, "write", write)
    DISK["full"] = False
    yield
    DISK["full"] = False


class DiskFillingLLM(SlowTestLLM):
    """Fills the disk during the first LLM call, i.e. in the middle of a step."""

    _filled: bool = PrivateAttr(default=False)

    async def acompletion(self, *args: Any, **kwargs: Any) -> LLMResponse:
        if not self._filled:
            self._filled = True
            DISK["full"] = True
        return await super().acompletion(*args, **kwargs)


class Recorder(Subscriber[Event]):
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def __call__(self, event: Event) -> None:
        self.events.append(event)


def _published_status(event: Event) -> str | None:
    if not isinstance(event, ConversationStateUpdateEvent):
        return None
    if event.key == FULL_STATE_KEY:
        return event.value["execution_status"]
    if event.key == "execution_status":
        return str(event.value)
    return None


@pytest.mark.timeout(60)
async def test_run_on_full_disk_reports_error_to_subscribers(tmp_path, full_disk):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service = ConversationService(conversations_dir=tmp_path / "persist")
    async with service:
        info = await start_conversation_with_test_llm(
            service,
            parent_llm=DiskFillingLLM.from_messages(
                [text_message("first"), text_message("second")]
            ),
            workspace_dir=str(workspace),
            usage_id="full-disk",
            initial_text=None,
        )
        event_service = await service.get_event_service(info.id)
        assert event_service is not None
        subscriber = Recorder()
        await event_service.subscribe_to_events(subscriber)
        subscriber.events.clear()

        await event_service.send_message(
            Message(role="user", content=[TextContent(text="hi")]), run=True
        )
        assert event_service._run_task is not None
        await event_service._run_task  # must not raise

        errors = [e for e in subscriber.events if isinstance(e, ConversationErrorEvent)]
        assert [e.code for e in errors] == ["OSError"]
        assert "No space left on device" in errors[0].detail
        statuses = [_published_status(e) for e in subscriber.events]
        assert statuses[-1] == "error"
        assert (
            await event_service._get_execution_status()
            == ConversationExecutionStatus.ERROR
        )
        conversation = event_service.get_conversation()
        assert not any(
            isinstance(e, ConversationErrorEvent) for e in conversation.state.events
        )

        DISK["full"] = False
        await event_service.send_message(
            Message(role="user", content=[TextContent(text="again")]), run=True
        )
        assert event_service._run_task is not None
        await event_service._run_task
        assert (
            await event_service._get_execution_status()
            == ConversationExecutionStatus.FINISHED
        )
        ids = [e.id for e in conversation.state.events]
        assert len(ids) == len(set(ids))
