"""A conversation reopens over an empty or corrupt saved event file (#6649)."""

import uuid
from pathlib import Path

import pytest

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation, LocalConversation
from openhands.sdk.conversation.event_store import UNREADABLE_EVENT_FILE_CODE
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.testing import TestLLM


def _agent(replies: list[str]) -> Agent:
    return Agent(
        llm=TestLLM.from_messages(
            [Message(role="assistant", content=[TextContent(text=r)]) for r in replies]
        ),
        tools=[],
    )


def _open(tmp_path: Path, cid: uuid.UUID, replies: list[str]) -> LocalConversation:
    conversation = Conversation(
        agent=_agent(replies),
        workspace=str(tmp_path / "ws"),
        persistence_dir=str(tmp_path / "persist"),
        conversation_id=cid,
        delete_on_close=False,
    )
    assert isinstance(conversation, LocalConversation)
    return conversation


@pytest.mark.parametrize("damage", ["{not json", ""])
def test_conversation_resumes_over_damaged_event_file(tmp_path: Path, damage: str):
    cid = uuid.uuid4()
    persist_dir = tmp_path / "persist"
    first = _open(tmp_path, cid, ["first answer", "second answer"])
    first.send_message("first")
    first.run()
    first.send_message("second")
    first.run()
    ids_before = [e.id for e in first.state.events]
    first.close()

    files = sorted(persist_dir.rglob("events/event-*.json"))
    assert len(files) == len(ids_before)
    damaged = files[len(files) // 2]
    damaged.write_text(damage)
    damaged_id = ids_before[len(files) // 2]

    resumed = _open(tmp_path, cid, ["third answer"])
    try:
        events_after = list(resumed.state.events)
        assert [e.id for e in events_after] == ids_before
        stand_in = events_after[ids_before.index(damaged_id)]
        assert isinstance(stand_in, ConversationErrorEvent)
        assert stand_in.code == UNREADABLE_EVENT_FILE_CODE

        # The resumed conversation keeps working and persists new events.
        resumed.send_message("third")
        resumed.run()
        assert resumed.state.events[-1].id not in ids_before
    finally:
        resumed.close()
