"""Cursor reservations survive the supported create/resume/navigate/fork APIs."""

from pathlib import Path
from uuid import UUID

import pytest

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.event import MessageEvent, SystemPromptEvent
from openhands.sdk.event.types import ROOT_PARENT_ID
from openhands.sdk.io import LocalFileStore


@pytest.fixture
def conversations(tmp_path: Path):
    opened: list[LocalConversation] = []

    def create(conversation_id: UUID | None = None):
        agent = (
            None
            if conversation_id
            else Agent(
                llm=LLM(model="test-provider/test-model", usage_id="offline"), tools=[]
            )
        )
        conversation = LocalConversation(
            agent=agent,
            persistence_dir=str(tmp_path / "persist"),
            workspace=str(tmp_path),
            conversation_id=conversation_id,
            visualizer=None,
        )
        assert isinstance(conversation, LocalConversation)
        opened.append(conversation)
        return conversation

    try:
        yield create
    finally:
        for conversation in reversed(opened):
            conversation.close()


def files(conversation: LocalConversation) -> list[Path]:
    directory = conversation.state.persistence_dir
    assert directory is not None
    return sorted(
        Path(directory).glob("events/event-*.json"),
        key=lambda path: int(path.name.split("-")[1]),
    )


def sequences(conversation: LocalConversation) -> list[int]:
    events = conversation.state.events
    return [events.get_seq(i) for i in range(len(events))]


def user_messages(conversation: LocalConversation) -> list[MessageEvent]:
    return [
        event
        for event in conversation.state.events
        if isinstance(event, MessageEvent) and event.source == "user"
    ]


def test_resume_after_lost_leaf_delivers_every_new_event_above_the_old_cursor(
    conversations,
):
    original = conversations()
    for text in ("first", "second", "third"):
        original.send_message(text)
    old_events = list(original.state.events)
    cursor = max(sequences(original))
    event_paths = files(original)
    conversation_id = original.id
    original.close()
    event_paths[-1].unlink()

    restored = conversations(conversation_id)
    restored.send_message("after recovery")
    new_events = [
        event
        for event in restored.state.events
        if event.id not in {e.id for e in old_events}
    ]
    assert new_events
    events = restored.state.events
    replayed = list(events[events.index_after_seq(cursor) :])
    assert [event.id for event in replayed] == [event.id for event in new_events]
    assert all(
        events.get_seq(events.get_index(event.id)) > cursor for event in new_events
    )
    assert len(set(sequences(restored))) == len(events)
    assert restored.state.leaf_event_id in events


def test_all_event_files_lost_rebuilds_system_prompt_without_reusing_sequences(
    conversations,
):
    original = conversations()
    original.send_message("before complete event loss")
    cursor = max(sequences(original))
    paths = files(original)
    conversation_id = original.id
    original.close()
    for path in paths:
        path.unlink()

    restored = conversations(conversation_id)
    restored.send_message("after complete event loss")
    assert min(sequences(restored)) > cursor
    assert any(isinstance(event, SystemPromptEvent) for event in restored.state.events)
    assert len(user_messages(restored)) == 1
    assert restored.state.leaf_event_id in restored.state.events
    assert len(set(sequences(restored))) == len(restored.state.events)


def test_navigating_after_recovery_changes_branch_not_sequence_high_water(
    conversations,
):
    original = conversations()
    original.send_message("branch root")
    root = user_messages(original)[-1]
    original.send_message("lost branch leaf")
    cursor = max(sequences(original))
    paths = files(original)
    conversation_id = original.id
    original.close()
    paths[-1].unlink()

    restored = conversations(conversation_id)
    restored.navigate_to(root.id)
    restored.send_message("replacement branch")
    replacement = user_messages(restored)[-1]
    events = restored.state.events
    assert events.get_seq(events.get_index(replacement.id)) > cursor
    assert replacement.parent_id == root.id
    assert [
        event.id
        for event in restored.state.view.events
        if isinstance(event, MessageEvent)
    ] == [root.id, replacement.id]


def test_explicit_new_root_after_recovery_keeps_a_fresh_cursor(conversations):
    original = conversations()
    original.send_message("old root")
    cursor = max(sequences(original))
    paths = files(original)
    conversation_id = original.id
    original.close()
    paths[-1].unlink()

    restored = conversations(conversation_id)
    restored.navigate_to(None)
    restored.send_message("new root")
    root = user_messages(restored)[-1]
    events = restored.state.events
    assert root.parent_id == ROOT_PARENT_ID
    assert events.get_seq(events.get_index(root.id)) > cursor
    assert [
        event.id
        for event in restored.state.view.events
        if isinstance(event, MessageEvent)
    ] == [root.id]


def test_fork_from_recovered_history_owns_an_independent_sequence_journal(
    conversations,
):
    original = conversations()
    original.send_message("first")
    original.send_message("second")
    original.send_message("third")
    cursor = max(sequences(original))
    paths = files(original)
    conversation_id = original.id
    original.close()
    paths[-1].unlink()
    restored = conversations(conversation_id)
    before_fork_ids = [event.id for event in restored.state.events]

    forked = restored.fork()
    try:
        assert forked.id != restored.id
        assert sequences(forked) == list(range(len(forked.state.events)))
        assert [event.id for event in forked.state.events] == before_fork_ids
        forked.send_message("fork only")
        fork_only = user_messages(forked)[-1]
        restored.send_message("source only")
        source_only = user_messages(restored)[-1]
        assert source_only.id not in forked.state.events
        assert fork_only.id not in restored.state.events
        assert max(sequences(restored)) > cursor
        assert len(sequences(forked)) == len(set(sequences(forked)))
        assert len(sequences(restored)) == len(set(sequences(restored)))
    finally:
        forked.close()


def test_interrupted_message_publication_recovers_through_resume(
    conversations, monkeypatch: pytest.MonkeyPatch
):
    original = conversations()
    original.send_message("committed")
    conversation_id = original.id
    committed = {event.id for event in original.state.events}
    cursor = max(sequences(original))
    actual_write = LocalFileStore.write
    interrupted = False

    def fail_once(store: LocalFileStore, path: str, contents: str | bytes):
        nonlocal interrupted
        if "/event-" in path and not interrupted:
            interrupted = True
            raise OSError("message publication interrupted")
        actual_write(store, path, contents)

    with monkeypatch.context() as patch:
        patch.setattr(LocalFileStore, "write", fail_once)
        with pytest.raises(OSError, match="message publication interrupted"):
            original.send_message("not committed")
    assert interrupted
    original.close()
    restored = conversations(conversation_id)
    restored.send_message("after failed publication")
    assert committed.issubset({event.id for event in restored.state.events})
    events = restored.state.events
    new = [event for event in events if event.id not in committed]
    assert new
    assert min(events.get_seq(events.get_index(event.id)) for event in new) > cursor + 1
