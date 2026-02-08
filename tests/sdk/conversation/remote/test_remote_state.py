"""Tests for RemoteState."""

import uuid
from unittest.mock import Mock

import httpx
import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.impl.remote_conversation import RemoteState
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.llm import LLM
from openhands.sdk.security.confirmation_policy import AlwaysConfirm


@pytest.fixture
def mock_client():
    """Create mock HTTP client."""
    return Mock(spec=httpx.Client)


@pytest.fixture
def conversation_id():
    """Test conversation ID."""
    return str(uuid.uuid4())


@pytest.fixture
def mock_agent():
    """Create a test agent."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"))
    return Agent(llm=llm, tools=[])


def create_mock_conversation_info(conversation_id: str, mock_agent: Agent, **overrides):
    """Create mock conversation info response."""
    default_info = {
        "id": conversation_id,
        "execution_status": "running",
        "confirmation_policy": {"kind": "NeverConfirm"},
        "activated_knowledge_skills": [],
        "agent": mock_agent.model_dump(mode="json"),
    }
    default_info.update(overrides)
    return default_info


def create_mock_api_response(data):
    """Create a mock API response."""
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = data
    return mock_response


def setup_mock_responses(mock_client, conversation_info):
    """Setup mock responses for events and conversation info."""
    mock_events_response = Mock()
    mock_events_response.raise_for_status.return_value = None
    mock_events_response.json.return_value = {"items": [], "next_page_id": None}

    mock_info_response = create_mock_api_response(conversation_info)

    mock_client.request.side_effect = [mock_events_response, mock_info_response]


def test_remote_state_initialization(mock_client, conversation_id):
    """Test RemoteState initialization and basic properties."""
    mock_events_response = Mock()
    mock_events_response.raise_for_status.return_value = None
    mock_events_response.json.return_value = {"items": [], "next_page_id": None}
    mock_client.request.return_value = mock_events_response

    state = RemoteState(mock_client, conversation_id)

    assert isinstance(state, RemoteState)
    assert str(state.id) == conversation_id

    # Events should be RemoteEventsList type
    from openhands.sdk.conversation.impl.remote_conversation import RemoteEventsList

    assert isinstance(state.events, RemoteEventsList)


@pytest.mark.parametrize(
    "status_value,expected",
    [
        ("running", ConversationExecutionStatus.RUNNING),
        ("paused", ConversationExecutionStatus.PAUSED),
        ("finished", ConversationExecutionStatus.FINISHED),
    ],
)
def test_remote_state_execution_status(
    mock_client, conversation_id, mock_agent, status_value, expected
):
    """Test execution_status property with different values."""
    conversation_info = create_mock_conversation_info(
        conversation_id, mock_agent, execution_status=status_value
    )
    setup_mock_responses(mock_client, conversation_info)

    state = RemoteState(mock_client, conversation_id)

    assert state.execution_status == expected


def test_remote_state_execution_status_setter_not_implemented(
    mock_client, conversation_id
):
    """Test that setting execution_status raises NotImplementedError."""
    mock_events_response = Mock()
    mock_events_response.raise_for_status.return_value = None
    mock_events_response.json.return_value = {"items": [], "next_page_id": None}
    mock_client.request.return_value = mock_events_response

    state = RemoteState(mock_client, conversation_id)

    with pytest.raises(
        NotImplementedError,
        match="Setting execution_status on RemoteState has no effect",
    ):
        state.execution_status = ConversationExecutionStatus.PAUSED


def test_remote_state_confirmation_policy(mock_client, conversation_id, mock_agent):
    """Test confirmation_policy property."""
    conversation_info = create_mock_conversation_info(
        conversation_id, mock_agent, confirmation_policy={"kind": "AlwaysConfirm"}
    )
    setup_mock_responses(mock_client, conversation_info)

    state = RemoteState(mock_client, conversation_id)
    policy = state.confirmation_policy

    assert isinstance(policy, AlwaysConfirm)


def test_remote_state_activated_knowledge_skills(
    mock_client, conversation_id, mock_agent
):
    """Test activated_knowledge_skills property."""
    microagents = ["agent1", "agent2", "agent3"]
    conversation_info = create_mock_conversation_info(
        conversation_id, mock_agent, activated_knowledge_skills=microagents
    )
    setup_mock_responses(mock_client, conversation_info)

    state = RemoteState(mock_client, conversation_id)

    assert state.activated_knowledge_skills == microagents


def test_remote_state_agent_property(mock_client, conversation_id, mock_agent):
    """Test agent property."""
    conversation_info = create_mock_conversation_info(conversation_id, mock_agent)
    setup_mock_responses(mock_client, conversation_info)

    state = RemoteState(mock_client, conversation_id)
    agent = state.agent

    assert isinstance(agent, Agent)


@pytest.mark.parametrize(
    "missing_field,property_name,error_match",
    [
        (
            "execution_status",
            "execution_status",
            "execution_status missing in conversation info",
        ),
        (
            "confirmation_policy",
            "confirmation_policy",
            "confirmation_policy missing in conversation info",
        ),
        ("agent", "agent", "agent missing in conversation info"),
    ],
)
def test_remote_state_missing_fields(
    mock_client, conversation_id, mock_agent, missing_field, property_name, error_match
):
    """Test error handling when required fields are missing."""
    conversation_info = create_mock_conversation_info(conversation_id, mock_agent)
    del conversation_info[missing_field]
    setup_mock_responses(mock_client, conversation_info)

    state = RemoteState(mock_client, conversation_id)

    with pytest.raises(RuntimeError, match=error_match):
        getattr(state, property_name)


def test_remote_state_model_dump(mock_client, conversation_id, mock_agent):
    """Test model_dump returns conversation info."""
    conversation_info = create_mock_conversation_info(conversation_id, mock_agent)
    setup_mock_responses(mock_client, conversation_info)

    state = RemoteState(mock_client, conversation_id)
    result = state.model_dump()

    assert result == conversation_info


def test_remote_state_model_dump_json(mock_client, conversation_id, mock_agent):
    """Test model_dump_json serializes to JSON string."""
    conversation_info = create_mock_conversation_info(conversation_id, mock_agent)
    setup_mock_responses(mock_client, conversation_info)

    state = RemoteState(mock_client, conversation_id)
    json_str = state.model_dump_json()

    assert isinstance(json_str, str)
    assert json_str.startswith("{")


def test_remote_state_context_manager(mock_client, conversation_id):
    """Test RemoteState can be used as context manager."""
    mock_events_response = Mock()
    mock_events_response.raise_for_status.return_value = None
    mock_events_response.json.return_value = {"items": [], "next_page_id": None}
    mock_client.request.return_value = mock_events_response

    state = RemoteState(mock_client, conversation_id)

    with state as ctx:
        assert ctx is state


def test_remote_state_api_error_handling(mock_client, conversation_id):
    """Test error propagation when conversation info API fails."""
    mock_events_response = Mock()
    mock_events_response.raise_for_status.return_value = None
    mock_events_response.json.return_value = {"items": [], "next_page_id": None}

    mock_request = Mock()
    mock_error_response = Mock()
    mock_error_response.status_code = 500

    mock_info_response = Mock()
    mock_info_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "API Error", request=mock_request, response=mock_error_response
    )

    mock_client.request.side_effect = [mock_events_response, mock_info_response]

    state = RemoteState(mock_client, conversation_id)

    with pytest.raises(httpx.HTTPStatusError):
        _ = state.execution_status
