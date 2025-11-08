"""Tests for conversation directory handling."""

import os
import tempfile
import uuid
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.llm import LLM
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture
def mock_agent():
    """Create a real agent for testing."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    return agent


def test_conversation_state_working_dir(mock_agent):
    """Test that ConversationState properly handles working_dir."""
    with tempfile.TemporaryDirectory() as temp_dir:
        working_dir = os.path.join(temp_dir, "work")
        os.makedirs(working_dir)

        state = ConversationState.create(
            id=uuid.uuid4(),
            agent=mock_agent,
            workspace=LocalWorkspace(working_dir=working_dir),
        )
        assert state.workspace.working_dir == working_dir
        assert state.workspace.working_dir is not None
        assert Path(state.workspace.working_dir).exists()


def test_conversation_state_persistence_dir(mock_agent):
    """Test that ConversationState properly handles persistence_dir."""
    with tempfile.TemporaryDirectory() as temp_dir:
        working_dir = os.path.join(temp_dir, "work")
        persistence_dir = os.path.join(temp_dir, "persist")
        os.makedirs(working_dir)

        state = ConversationState.create(
            id=uuid.uuid4(),
            agent=mock_agent,
            workspace=LocalWorkspace(working_dir=working_dir),
            persistence_dir=persistence_dir,
        )
        # ConversationState.create() uses persistence_dir directly (no subdirectory)
        assert state.persistence_dir == persistence_dir
        # persistence_dir should be created automatically
        assert state.persistence_dir is not None
        assert Path(state.persistence_dir).exists()


def test_conversation_state_both_directories(mock_agent):
    """Test that ConversationState handles both directories."""
    with tempfile.TemporaryDirectory() as temp_dir:
        working_dir = os.path.join(temp_dir, "work")
        persistence_dir = os.path.join(temp_dir, "persist")
        os.makedirs(working_dir)

        state = ConversationState.create(
            id=uuid.uuid4(),
            agent=mock_agent,
            persistence_dir=persistence_dir,
            workspace=LocalWorkspace(working_dir=working_dir),
        )
        assert state.workspace.working_dir == working_dir
        # ConversationState.create() uses persistence_dir directly (no subdirectory)
        assert state.persistence_dir == persistence_dir
        assert state.workspace.working_dir is not None
        assert state.persistence_dir is not None
        assert Path(state.workspace.working_dir).exists()
        assert Path(state.persistence_dir).exists()


def test_conversation_factory_with_directories(mock_agent):
    """Test that Conversation factory properly handles directory parameters."""
    with tempfile.TemporaryDirectory() as temp_dir:
        working_dir = os.path.join(temp_dir, "work")
        persistence_dir = os.path.join(temp_dir, "persist")
        os.makedirs(working_dir)

        conversation = Conversation(
            agent=mock_agent,
            workspace=LocalWorkspace(working_dir=working_dir),
            persistence_dir=persistence_dir,
        )

        assert conversation.state.workspace.working_dir == working_dir
        # persistence_dir should include conversation ID subdirectory
        expected_dir = os.path.join(persistence_dir, conversation.state.id.hex)
        assert conversation.state.persistence_dir == expected_dir


def test_conversation_factory_default_directories(mock_agent):
    """Test that Conversation factory uses default directories when not specified."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Change to temp directory to avoid conflicts with existing state
        original_cwd = os.getcwd()
        try:
            os.chdir(temp_dir)
            conversation = Conversation(agent=mock_agent)

            # Should use "workspace/project" as default working directory
            assert conversation.state.workspace.working_dir == "workspace/project"
            assert conversation.state.persistence_dir is None
        finally:
            os.chdir(original_cwd)


def test_conversation_factory_working_dir_only(mock_agent):
    """Test that Conversation factory handles working_dir only."""
    with tempfile.TemporaryDirectory() as temp_dir:
        working_dir = os.path.join(temp_dir, "work")
        os.makedirs(working_dir)

        conversation = Conversation(agent=mock_agent, workspace=working_dir)

        assert conversation.state.workspace.working_dir == working_dir
        assert conversation.state.persistence_dir is None


def test_conversation_factory_persistence_dir_only(mock_agent):
    """Test that Conversation factory handles persistence_dir only."""
    with tempfile.TemporaryDirectory() as temp_dir:
        persistence_dir = os.path.join(temp_dir, "persist")

        conversation = Conversation(agent=mock_agent, persistence_dir=persistence_dir)

        # Should use default "workspace/project" as working directory
        assert conversation.state.workspace.working_dir == "workspace/project"
        # persistence_dir should include conversation ID subdirectory
        expected_dir = os.path.join(persistence_dir, conversation.state.id.hex)
        assert conversation.state.persistence_dir == expected_dir
