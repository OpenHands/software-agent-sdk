"""Tests for Conversation factory functionality."""

import tempfile
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from pydantic import SecretStr

from openhands.sdk import Agent, Conversation
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.impl.remote_conversation import RemoteConversation
from openhands.sdk.llm import LLM
from openhands.sdk.workspace import RemoteWorkspace


@pytest.fixture
def agent():
    """Create test agent."""
    llm = LLM(model="gpt-4", api_key=SecretStr("test-key"))
    return Agent(llm=llm, tools=[])


@pytest.fixture
def remote_workspace():
    """Create RemoteWorkspace with mocked client."""
    workspace = RemoteWorkspace(
        host="http://localhost:8000", working_dir="/workspace/project"
    )

    # Mock the workspace client
    mock_client = Mock()
    workspace._client = mock_client

    # Mock conversation creation response
    conversation_id = str(uuid.uuid4())
    mock_conv_response = Mock()
    mock_conv_response.raise_for_status.return_value = None
    mock_conv_response.json.return_value = {"id": conversation_id}

    # Mock events response
    mock_events_response = Mock()
    mock_events_response.raise_for_status.return_value = None
    mock_events_response.json.return_value = {"items": [], "next_page_id": None}

    mock_client.request.side_effect = [mock_conv_response, mock_events_response]

    return workspace


def test_conversation_factory_creates_local_by_default(agent):
    """Test factory creates LocalConversation when no workspace specified."""
    conversation = Conversation(agent=agent)

    assert isinstance(conversation, LocalConversation)


@patch("openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient")
def test_conversation_factory_creates_remote_with_workspace(
    mock_ws_client, agent, remote_workspace
):
    """Test factory creates RemoteConversation with RemoteWorkspace."""
    conversation = Conversation(agent=agent, workspace=remote_workspace)

    assert isinstance(conversation, RemoteConversation)


def test_conversation_factory_forwards_local_parameters(agent):
    """Test factory forwards parameters to LocalConversation correctly."""
    conversation = Conversation(
        agent=agent,
        max_iteration_per_run=100,
        stuck_detection=False,
        visualizer=None,
    )

    assert isinstance(conversation, LocalConversation)
    assert conversation.max_iteration_per_run == 100


@patch("openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient")
def test_conversation_factory_forwards_remote_parameters(
    mock_ws_client, agent, remote_workspace
):
    """Test factory forwards parameters to RemoteConversation correctly."""
    conversation = Conversation(
        agent=agent,
        workspace=remote_workspace,
        max_iteration_per_run=200,
        stuck_detection=True,
    )

    assert isinstance(conversation, RemoteConversation)
    assert conversation.max_iteration_per_run == 200


def test_conversation_factory_string_workspace_creates_local(agent):
    """Test that string workspace creates LocalConversation."""
    conversation = Conversation(agent=agent, workspace="")

    assert isinstance(conversation, LocalConversation)


@patch("openhands.sdk.conversation.impl.remote_conversation.WebSocketCallbackClient")
def test_conversation_factory_type_inference(mock_ws_client, agent, remote_workspace):
    """Test that type hints work correctly for both conversation types."""
    local_conv = Conversation(agent=agent)
    remote_conv = Conversation(agent=agent, workspace=remote_workspace)

    assert isinstance(local_conv, LocalConversation)
    assert isinstance(remote_conv, RemoteConversation)


def test_conversation_factory_load_repo_skills_with_skills_dir(agent):
    """Test load_repo_skills=True loads skills from workspace."""
    with tempfile.TemporaryDirectory() as temp_dir:
        workspace = Path(temp_dir)

        # Create .openhands/skills directory with a skill
        skills_dir = workspace / ".openhands" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "test_skill.md").write_text(
            "---\nname: test_skill\n---\nTest skill content."
        )

        conversation = Conversation(
            agent=agent,
            workspace=workspace,
            load_repo_skills=True,
        )

        assert isinstance(conversation, LocalConversation)
        assert conversation.agent.agent_context is not None
        skill_names = [s.name for s in conversation.agent.agent_context.skills]
        assert "test_skill" in skill_names


def test_conversation_factory_load_repo_skills_merges_with_existing(agent):
    """Test that repo skills merge with existing agent context skills."""
    from openhands.sdk.context.agent_context import AgentContext
    from openhands.sdk.context.skills import Skill

    with tempfile.TemporaryDirectory() as temp_dir:
        workspace = Path(temp_dir)

        # Create .openhands/skills directory with a skill
        skills_dir = workspace / ".openhands" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "repo_skill.md").write_text(
            "---\nname: repo_skill\n---\nRepo skill content."
        )

        # Create agent with existing skill in context
        existing_skill = Skill(
            name="existing_skill",
            content="Existing skill content.",
            trigger=None,
        )
        agent_with_context = Agent(
            llm=agent.llm,
            tools=[],
            agent_context=AgentContext(skills=[existing_skill]),
        )

        conversation = Conversation(
            agent=agent_with_context,
            workspace=workspace,
            load_repo_skills=True,
        )

        assert isinstance(conversation, LocalConversation)
        assert conversation.agent.agent_context is not None
        skill_names = [s.name for s in conversation.agent.agent_context.skills]
        # Both skills should be present
        assert "existing_skill" in skill_names
        assert "repo_skill" in skill_names


def test_conversation_factory_load_repo_skills_existing_takes_precedence(agent):
    """Test that existing skills take precedence over repo skills with same name."""
    from openhands.sdk.context.agent_context import AgentContext
    from openhands.sdk.context.skills import Skill

    with tempfile.TemporaryDirectory() as temp_dir:
        workspace = Path(temp_dir)

        # Create .openhands/skills directory with a skill
        skills_dir = workspace / ".openhands" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "duplicate.md").write_text(
            "---\nname: duplicate\n---\nRepo skill content."
        )

        # Create agent with skill that has same name
        existing_skill = Skill(
            name="duplicate",
            content="Existing skill content.",
            trigger=None,
        )
        agent_with_context = Agent(
            llm=agent.llm,
            tools=[],
            agent_context=AgentContext(skills=[existing_skill]),
        )

        conversation = Conversation(
            agent=agent_with_context,
            workspace=workspace,
            load_repo_skills=True,
        )

        assert isinstance(conversation, LocalConversation)
        assert conversation.agent.agent_context is not None
        # Only one skill with that name
        duplicates = [
            s for s in conversation.agent.agent_context.skills if s.name == "duplicate"
        ]
        assert len(duplicates) == 1
        # Existing skill takes precedence
        assert duplicates[0].content == "Existing skill content."
