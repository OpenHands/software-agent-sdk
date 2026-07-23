"""Tests for custom FileStore injection in local conversations."""

import logging
import uuid
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.io import InMemoryFileStore
from openhands.sdk.llm import LLM
from openhands.sdk.workspace import LocalWorkspace


def create_test_agent() -> Agent:
    """Create a test agent."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    return Agent(llm=llm, tools=[])


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
