"""Tests for custom FileStore injection in local conversations."""

import logging
import uuid
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.io import InMemoryFileStore, LocalFileStore
from openhands.sdk.llm import LLM
from openhands.sdk.workspace import LocalWorkspace


def create_test_agent() -> Agent:
    """Create a test agent."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    return Agent(llm=llm, tools=[])


def test_injected_local_store_survives_conversation_close_and_resume(tmp_path):
    """The caller can reuse its real store after a conversation is closed."""
    file_store = LocalFileStore(str(tmp_path / "store"))
    conversation_id = uuid.uuid4()
    conversation = LocalConversation(
        agent=create_test_agent(),
        workspace=tmp_path / "workspace",
        conversation_id=conversation_id,
        file_store=file_store,
        visualizer=None,
    )
    resumed = None
    try:
        conversation.send_message("before close")
        conversation.close()
        file_store.write("caller.txt", "still writable")
        resumed = LocalConversation(
            agent=create_test_agent(),
            workspace=tmp_path / "workspace",
            conversation_id=conversation_id,
            file_store=file_store,
            visualizer=None,
        )
        resumed.send_message("after resume")
        resumed.close()
        assert file_store.read("caller.txt") == "still writable"
        assert any(
            "after resume" in event.model_dump_json() for event in resumed.state.events
        )
    finally:
        conversation.close()
        if resumed is not None:
            resumed.close()
        file_store.close()


def test_failed_resume_closes_owned_store_and_preserves_error(tmp_path, monkeypatch):
    """A failed factory call cannot strand a writer before ownership is returned."""
    conversation_id = uuid.uuid4()
    persistence_dir = str(tmp_path / "store")
    state = ConversationState.create(
        id=conversation_id,
        agent=create_test_agent(),
        workspace=LocalWorkspace(working_dir=tmp_path / "workspace"),
        persistence_dir=persistence_dir,
    )
    state.close()
    stores: list[LocalFileStore] = []
    original_write = LocalFileStore.write

    def fail_second_write(self, path, contents):
        if stores:
            raise OSError("resume snapshot failed")
        stores.append(self)
        original_write(self, path, contents)
        self.flush()

    monkeypatch.setattr(LocalFileStore, "write", fail_second_write)
    changed_agent = create_test_agent()
    changed_agent.llm.temperature = 0.5
    try:
        with pytest.raises(OSError, match="resume snapshot failed"):
            ConversationState.create(
                id=conversation_id,
                agent=changed_agent,
                workspace=LocalWorkspace(working_dir=tmp_path / "other-workspace"),
                persistence_dir=persistence_dir,
                max_iterations=100,
            )
        assert len(stores) == 1
        writer = stores[0]._durability
        assert writer is not None and writer._thread is not None
        assert not writer._thread.is_alive()
        with pytest.raises(RuntimeError, match="closed"):
            original_write(stores[0], "late.txt", "v")
    finally:
        for store in stores:
            store.close()


def test_conversation_state_uses_injected_file_store(tmp_path, caplog):
    """ConversationState.create uses an injected FileStore without warning."""
    file_store = InMemoryFileStore()
    conversation_id = uuid.uuid4()
    workspace = LocalWorkspace(working_dir=tmp_path / "workspace")

    with caplog.at_level(logging.WARNING):
        state = ConversationState.create(
            id=conversation_id,
            agent=create_test_agent(),
            workspace=workspace,
            file_store=file_store,
        )

    assert state.id == conversation_id
    assert state.persistence_dir is None
    assert state._fs is file_store
    assert file_store.exists("base_state.json")
    assert not any(
        "No persistence_dir provided; falling back to InMemoryFileStore"
        in record.message
        for record in caplog.records
    )

    resumed_state = ConversationState.create(
        id=conversation_id,
        agent=create_test_agent(),
        workspace=workspace,
        file_store=file_store,
    )

    assert resumed_state.id == conversation_id
    assert resumed_state._fs is file_store


def test_conversation_state_file_store_takes_precedence_over_persistence_dir(tmp_path):
    """Injected FileStore stores state while persistence_dir remains metadata."""
    file_store = InMemoryFileStore()
    persistence_dir = tmp_path / "persistence"

    state = ConversationState.create(
        id=uuid.uuid4(),
        agent=create_test_agent(),
        workspace=LocalWorkspace(working_dir=tmp_path / "workspace"),
        persistence_dir=str(persistence_dir),
        file_store=file_store,
    )

    assert state.persistence_dir == str(persistence_dir)
    assert state._fs is file_store
    assert file_store.exists("base_state.json")
    assert not (persistence_dir / "base_state.json").exists()


def test_local_conversation_uses_injected_file_store(tmp_path):
    """LocalConversation forwards the injected FileStore to ConversationState."""
    file_store = InMemoryFileStore()
    conversation_id = uuid.uuid4()

    conversation = LocalConversation(
        agent=create_test_agent(),
        workspace=tmp_path / "workspace",
        conversation_id=conversation_id,
        file_store=file_store,
        visualizer=None,
    )

    assert conversation.id == conversation_id
    assert conversation.state.persistence_dir is None
    assert conversation.state._fs is file_store
    assert file_store.exists("base_state.json")

    resumed_conversation = LocalConversation(
        agent=create_test_agent(),
        workspace=tmp_path / "workspace",
        conversation_id=conversation_id,
        file_store=file_store,
        visualizer=None,
    )

    assert resumed_conversation.id == conversation_id
    assert resumed_conversation.state._fs is file_store


def test_local_conversation_keeps_persistence_dir_with_injected_file_store(tmp_path):
    """persistence_dir still sets observation paths when FileStore is injected."""
    file_store = InMemoryFileStore()
    persistence_root = tmp_path / "persistence"
    conversation_id = uuid.uuid4()

    conversation = LocalConversation(
        agent=create_test_agent(),
        workspace=tmp_path / "workspace",
        persistence_dir=persistence_root,
        conversation_id=conversation_id,
        file_store=file_store,
        visualizer=None,
    )

    expected_persistence_dir = Path(persistence_root) / conversation_id.hex
    assert conversation.state.persistence_dir == str(expected_persistence_dir)
    assert conversation.state.env_observation_persistence_dir == str(
        expected_persistence_dir / "observations"
    )
    assert conversation.state._fs is file_store
    assert file_store.exists("base_state.json")
    assert not (expected_persistence_dir / "base_state.json").exists()
