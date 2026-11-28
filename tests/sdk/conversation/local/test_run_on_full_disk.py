"""A conversation run whose storage fills up mid-step (#7102)."""

import asyncio
import errno
import json

import pytest

from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.conversation.types import (
    ConversationCallbackType,
    ConversationTokenCallbackType,
)
from openhands.sdk.event import MessageEvent
from openhands.sdk.io import LocalFileStore
from openhands.sdk.llm import LLM, Message, TextContent


class FullDiskFileStore(LocalFileStore):
    """Writes fail with ENOSPC while ``full`` is set, like a full filesystem."""

    full: bool = False

    def write(self, path: str, contents: str | bytes) -> None:
        if self.full:
            raise OSError(errno.ENOSPC, "No space left on device")
        super().write(path, contents)


def _reply(text: str) -> MessageEvent:
    return MessageEvent(
        source="agent",
        llm_message=Message(role="assistant", content=[TextContent(text=text)]),
    )


class DiskFillingAgent(AgentBase):
    """Fills the disk in the middle of its first step, then emits a reply."""

    def step(
        self,
        conversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,  # noqa: ARG002
    ) -> None:
        store = conversation.state._fs
        assert isinstance(store, FullDiskFileStore)
        store.full = True
        on_event(_reply("done"))


@pytest.fixture
def full_disk_conversation(tmp_path):
    store = FullDiskFileStore(str(tmp_path / "state"))
    agent = DiskFillingAgent(llm=LLM(model="gpt-4o-mini", usage_id="test"), tools=[])
    conv = LocalConversation(
        agent=agent,
        workspace=str(tmp_path / "ws"),
        file_store=store,
        visualizer=None,
        stuck_detection=False,
    )
    conv.send_message("hi")
    return conv, store


def _persisted_status(store: FullDiskFileStore) -> str:
    return json.loads(store.read("base_state.json"))["execution_status"]


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_run_on_full_disk_raises_conversation_run_error(full_disk_conversation, mode):
    conv, store = full_disk_conversation

    with pytest.raises(ConversationRunError) as excinfo:
        if mode == "sync":
            conv.run()
        else:
            asyncio.run(conv.arun())

    cause = excinfo.value.__cause__
    assert isinstance(cause, OSError) and cause.errno == errno.ENOSPC
    assert excinfo.value.conversation_error is None  # it could not be recorded
    assert conv.state.execution_status == ConversationExecutionStatus.ERROR
    assert _persisted_status(store) == "running"  # last good snapshot

    # A block that changes nothing must not raise the stale save error.
    with conv.state:
        pass

    store.full = False
    with conv.state:
        pass
    assert _persisted_status(store) == "error"


def test_failed_flush_does_not_replace_propagating_exception(full_disk_conversation):
    conv, store = full_disk_conversation
    store.full = True

    with pytest.raises(ValueError, match="original"):
        with conv.state:
            conv.state.max_iterations = 7
            raise ValueError("original")

    store.full = False
    with conv.state:
        pass
    assert json.loads(store.read("base_state.json"))["max_iterations"] == 7


def test_new_change_after_failed_save_still_raises(full_disk_conversation):
    conv, store = full_disk_conversation
    store.full = True
    with pytest.raises(OSError):
        with conv.state:
            conv.state.max_iterations = 7

    with pytest.raises(OSError):
        with conv.state:
            conv.state.max_iterations = 8
