"""Tests for conversation_router.py endpoints."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.conversation_router import conversation_router
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import (
    ConversationInfo,
    ConversationPage,
    ConversationSortOrder,
    SendMessageRequest,
    StartConversationRequest,
)
from openhands.agent_server.utils import utc_now
from openhands.sdk import LLM, Agent, TextContent, Tool
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture
def client():
    """Create a test client for the FastAPI app without authentication."""
    app = FastAPI()
    app.include_router(conversation_router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def sample_conversation_id():
    """Return a sample conversation ID."""
    return uuid4()


@pytest.fixture
def sample_conversation_info():
    """Create a sample ConversationInfo for testing."""
    conversation_id = uuid4()
    now = utc_now()
    return ConversationInfo(
        id=conversation_id,
        agent=Agent(
            llm=LLM(
                model="gpt-4o",
                api_key=SecretStr("test-key"),
                usage_id="test-llm",
            ),
            tools=[Tool(name="TerminalTool")],
        ),
        workspace=LocalWorkspace(working_dir="/tmp/test"),
        execution_status=ConversationExecutionStatus.IDLE,
        title="Test Conversation",
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def mock_conversation_service():
    """Create a mock ConversationService for testing."""
    service = AsyncMock(spec=ConversationService)
    return service


@pytest.fixture
def mock_event_service():
    """Create a mock EventService for testing."""
    service = AsyncMock(spec=EventService)
    return service


@pytest.fixture
def llm_security_analyzer():
    """Create an LLMSecurityAnalyzer for testing."""
    return LLMSecurityAnalyzer()


@pytest.fixture
def sample_start_conversation_request():
    """Create a sample StartConversationRequest for testing."""
    return StartConversationRequest(
        agent=Agent(
            llm=LLM(
                model="gpt-4o",
                api_key=SecretStr("test-key"),
                usage_id="test-llm",
            ),
            tools=[Tool(name="TerminalTool")],
        ),
        workspace=LocalWorkspace(working_dir="/tmp/test"),
        initial_message=SendMessageRequest(
            role="user", content=[TextContent(text="Hello, world!")]
        ),
    )


def test_search_conversations_default_params(
    client, mock_conversation_service, sample_conversation_info
):
    """Test search_conversations endpoint with default parameters."""

    # Mock the service response
    mock_page = ConversationPage(items=[sample_conversation_info], next_page_id=None)
    mock_conversation_service.search_conversations.return_value = mock_page

    # Override the dependency
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.get("/api/conversations/search")

        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "next_page_id" in data
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == str(sample_conversation_info.id)

        # Verify service was called with default parameters
        mock_conversation_service.search_conversations.assert_called_once_with(
            None, 100, None, ConversationSortOrder.CREATED_AT_DESC
        )
    finally:
        client.app.dependency_overrides.clear()


def test_search_conversations_with_all_params(
    client, mock_conversation_service, sample_conversation_info
):
    """Test search_conversations endpoint with all parameters."""

    # Mock the service response
    mock_page = ConversationPage(
        items=[sample_conversation_info], next_page_id="next_page"
    )
    mock_conversation_service.search_conversations.return_value = mock_page

    # Override the dependency
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.get(
            "/api/conversations/search",
            params={
                "page_id": "test_page",
                "limit": 50,
                "status": ConversationExecutionStatus.IDLE.value,
                "sort_order": ConversationSortOrder.UPDATED_AT_DESC.value,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["next_page_id"] == "next_page"

        # Verify service was called with correct parameters
        mock_conversation_service.search_conversations.assert_called_once_with(
            "test_page",
            50,
            ConversationExecutionStatus.IDLE,
            ConversationSortOrder.UPDATED_AT_DESC,
        )
    finally:
        client.app.dependency_overrides.clear()


def test_search_conversations_limit_validation(client, mock_conversation_service):
    """Test search_conversations endpoint with invalid limit values."""

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        # Test limit too low (gt=0 means > 0, so 0 should fail)
        response = client.get("/api/conversations/search", params={"limit": 0})
        assert response.status_code == 422

        # Test limit too high - endpoint has FastAPI validation (lte=100) and assertion
        # The assertion in the endpoint will cause an AssertionError to be raised
        with pytest.raises(AssertionError):
            response = client.get("/api/conversations/search", params={"limit": 101})

        # Test valid limit
        mock_conversation_service.search_conversations.return_value = ConversationPage(
            items=[], next_page_id=None
        )
        response = client.get("/api/conversations/search", params={"limit": 50})
        assert response.status_code == 200
    finally:
        client.app.dependency_overrides.clear()


def test_search_conversations_empty_result(client, mock_conversation_service):
    """Test search_conversations endpoint with empty result."""

    # Mock empty response
    mock_page = ConversationPage(items=[], next_page_id=None)
    mock_conversation_service.search_conversations.return_value = mock_page

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.get("/api/conversations/search")

        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["next_page_id"] is None
    finally:
        client.app.dependency_overrides.clear()


def test_count_conversations_no_filter(client, mock_conversation_service):
    """Test count_conversations endpoint without status filter."""

    # Mock the service response
    mock_conversation_service.count_conversations.return_value = 5

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.get("/api/conversations/count")

        assert response.status_code == 200
        assert response.json() == 5

        # Verify service was called with no status filter
        mock_conversation_service.count_conversations.assert_called_once_with(None)
    finally:
        client.app.dependency_overrides.clear()


def test_count_conversations_with_status_filter(client, mock_conversation_service):
    """Test count_conversations endpoint with status filter."""

    # Mock the service response
    mock_conversation_service.count_conversations.return_value = 3

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.get(
            "/api/conversations/count",
            params={"status": ConversationExecutionStatus.RUNNING.value},
        )

        assert response.status_code == 200
        assert response.json() == 3

        # Verify service was called with status filter
        mock_conversation_service.count_conversations.assert_called_once_with(
            ConversationExecutionStatus.RUNNING
        )
    finally:
        client.app.dependency_overrides.clear()


def test_count_conversations_zero_result(client, mock_conversation_service):
    """Test count_conversations endpoint with zero result."""

    # Mock zero count response
    mock_conversation_service.count_conversations.return_value = 0

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.get("/api/conversations/count")

        assert response.status_code == 200
        assert response.json() == 0
    finally:
        client.app.dependency_overrides.clear()


def test_get_conversation_success(
    client, mock_conversation_service, sample_conversation_info, sample_conversation_id
):
    """Test get_conversation endpoint with existing conversation."""

    # Mock the service response
    mock_conversation_service.get_conversation.return_value = sample_conversation_info

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.get(f"/api/conversations/{sample_conversation_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(sample_conversation_info.id)
        assert data["title"] == sample_conversation_info.title

        # Verify service was called with correct conversation ID
        mock_conversation_service.get_conversation.assert_called_once_with(
            sample_conversation_id
        )
    finally:
        client.app.dependency_overrides.clear()


def test_get_conversation_not_found(
    client, mock_conversation_service, sample_conversation_id
):
    """Test get_conversation endpoint with non-existent conversation."""

    # Mock the service to return None (conversation not found)
    mock_conversation_service.get_conversation.return_value = None

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.get(f"/api/conversations/{sample_conversation_id}")

        assert response.status_code == 404

        # Verify service was called with correct conversation ID
        mock_conversation_service.get_conversation.assert_called_once_with(
            sample_conversation_id
        )
    finally:
        client.app.dependency_overrides.clear()


def test_get_conversation_invalid_uuid(client, mock_conversation_service):
    """Test get_conversation endpoint with invalid UUID."""

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.get("/api/conversations/invalid-uuid")

        assert response.status_code == 422  # Validation error for invalid UUID
    finally:
        client.app.dependency_overrides.clear()


def test_batch_get_conversations_success(
    client, mock_conversation_service, sample_conversation_info
):
    """Test batch_get_conversations endpoint with valid IDs."""

    # Create additional conversation info for testing
    conversation_id_1 = uuid4()
    conversation_id_2 = uuid4()

    # Mock the service response - return one found, one None
    mock_conversation_service.batch_get_conversations.return_value = [
        sample_conversation_info,
        None,
    ]

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.get(
            "/api/conversations",
            params={"ids": [str(conversation_id_1), str(conversation_id_2)]},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["id"] == str(sample_conversation_info.id)
        assert data[1] is None

        # Verify service was called with correct IDs
        mock_conversation_service.batch_get_conversations.assert_called_once_with(
            [conversation_id_1, conversation_id_2]
        )
    finally:
        client.app.dependency_overrides.clear()


def test_batch_get_conversations_empty_list(client, mock_conversation_service):
    """Test batch_get_conversations endpoint with empty ID list."""

    # Mock empty response
    mock_conversation_service.batch_get_conversations.return_value = []

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        # FastAPI requires at least one value for query parameters that expect a list
        # So we'll test with a single valid UUID instead
        test_id = str(uuid4())
        mock_conversation_service.batch_get_conversations.return_value = [None]

        response = client.get("/api/conversations", params={"ids": [test_id]})

        assert response.status_code == 200
        data = response.json()
        assert data == [None]

        # Verify service was called
        mock_conversation_service.batch_get_conversations.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


def test_batch_get_conversations_too_many_ids(client, mock_conversation_service):
    """Test batch_get_conversations endpoint with too many IDs."""

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        # The assertion is len(ids) < 100, so 100 should fail with AssertionError
        many_ids = [str(uuid4()) for _ in range(100)]
        with pytest.raises(AssertionError):
            response = client.get("/api/conversations", params={"ids": many_ids})

        # Test with 99 IDs (should work)
        mock_conversation_service.batch_get_conversations.return_value = [None] * 99
        valid_ids = [str(uuid4()) for _ in range(99)]
        response = client.get("/api/conversations", params={"ids": valid_ids})
        assert response.status_code == 200
    finally:
        client.app.dependency_overrides.clear()


def test_batch_get_conversations_invalid_uuid(client, mock_conversation_service):
    """Test batch_get_conversations endpoint with invalid UUID."""

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.get("/api/conversations", params={"ids": ["invalid-uuid"]})

        assert response.status_code == 422  # Validation error for invalid UUID
    finally:
        client.app.dependency_overrides.clear()


def test_start_conversation_new(
    client, mock_conversation_service, sample_conversation_info
):
    """Test start_conversation endpoint creating a new conversation."""

    # Mock the service response - new conversation created
    mock_conversation_service.start_conversation.return_value = (
        sample_conversation_info,
        True,
    )

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        # Create request data with proper serialization
        request_data = {
            "agent": {
                "llm": {
                    "model": "gpt-4o",
                    "api_key": "test-key",
                    "usage_id": "test-llm",
                },
                "tools": [{"name": "TerminalTool"}],
            },
            "workspace": {"working_dir": "/tmp/test"},
            "initial_message": {
                "role": "user",
                "content": [{"type": "text", "text": "Hello, world!"}],
            },
        }

        response = client.post("/api/conversations", json=request_data)

        assert response.status_code == 201  # Created
        data = response.json()
        assert data["id"] == str(sample_conversation_info.id)
        assert data["title"] == sample_conversation_info.title

        # Verify service was called
        mock_conversation_service.start_conversation.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


def test_start_conversation_existing(
    client, mock_conversation_service, sample_conversation_info
):
    """Test start_conversation endpoint with existing conversation."""

    # Mock the service response - existing conversation returned
    mock_conversation_service.start_conversation.return_value = (
        sample_conversation_info,
        False,
    )

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        # Create request data with proper serialization
        request_data = {
            "agent": {
                "llm": {
                    "model": "gpt-4o",
                    "api_key": "test-key",
                    "usage_id": "test-llm",
                },
                "tools": [{"name": "TerminalTool"}],
            },
            "workspace": {"working_dir": "/tmp/test"},
        }

        response = client.post("/api/conversations", json=request_data)

        assert response.status_code == 200  # OK (existing)
        data = response.json()
        assert data["id"] == str(sample_conversation_info.id)

        # Verify service was called
        mock_conversation_service.start_conversation.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


def test_start_conversation_invalid_request(client, mock_conversation_service):
    """Test start_conversation endpoint with invalid request data."""

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        # Test with missing required fields
        invalid_request = {"invalid": "data"}

        response = client.post("/api/conversations", json=invalid_request)

        assert response.status_code == 422  # Validation error
    finally:
        client.app.dependency_overrides.clear()


def test_start_conversation_minimal_request(
    client, mock_conversation_service, sample_conversation_info
):
    """Test start_conversation endpoint with minimal valid request."""

    # Mock the service response
    mock_conversation_service.start_conversation.return_value = (
        sample_conversation_info,
        True,
    )

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        # Create minimal valid request
        minimal_request = {
            "agent": {
                "llm": {
                    "model": "gpt-4o",
                    "api_key": "test-key",
                    "usage_id": "test-llm",
                },
                "tools": [{"name": "TerminalTool"}],
            },
            "workspace": {"working_dir": "/tmp/test"},
        }

        response = client.post("/api/conversations", json=minimal_request)

        assert response.status_code == 201
        data = response.json()
        assert data["id"] == str(sample_conversation_info.id)
    finally:
        client.app.dependency_overrides.clear()


def test_pause_conversation_success(
    client, mock_conversation_service, sample_conversation_id
):
    """Test pause_conversation endpoint with successful pause."""

    # Mock the service response - pause successful
    mock_conversation_service.pause_conversation.return_value = True

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.post(f"/api/conversations/{sample_conversation_id}/pause")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify service was called with correct conversation ID
        mock_conversation_service.pause_conversation.assert_called_once_with(
            sample_conversation_id
        )
    finally:
        client.app.dependency_overrides.clear()


def test_pause_conversation_failure(
    client, mock_conversation_service, sample_conversation_id
):
    """Test pause_conversation endpoint with pause failure."""

    # Mock the service response - pause failed
    mock_conversation_service.pause_conversation.return_value = False

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.post(f"/api/conversations/{sample_conversation_id}/pause")

        assert response.status_code == 400  # Bad Request

        # Verify service was called
        mock_conversation_service.pause_conversation.assert_called_once_with(
            sample_conversation_id
        )
    finally:
        client.app.dependency_overrides.clear()


def test_delete_conversation_success(
    client, mock_conversation_service, sample_conversation_id
):
    """Test delete_conversation endpoint with successful deletion."""

    # Mock the service response - deletion successful
    mock_conversation_service.delete_conversation.return_value = True

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.delete(f"/api/conversations/{sample_conversation_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify service was called with correct conversation ID
        mock_conversation_service.delete_conversation.assert_called_once_with(
            sample_conversation_id
        )
    finally:
        client.app.dependency_overrides.clear()


def test_delete_conversation_failure(
    client, mock_conversation_service, sample_conversation_id
):
    """Test delete_conversation endpoint with deletion failure."""

    # Mock the service response - deletion failed
    mock_conversation_service.delete_conversation.return_value = False

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.delete(f"/api/conversations/{sample_conversation_id}")

        assert response.status_code == 400  # Bad Request

        # Verify service was called
        mock_conversation_service.delete_conversation.assert_called_once_with(
            sample_conversation_id
        )
    finally:
        client.app.dependency_overrides.clear()


def test_run_conversation_success(
    client, mock_conversation_service, mock_event_service, sample_conversation_id
):
    """Test run_conversation endpoint with successful run."""

    # Mock the service responses
    mock_conversation_service.get_event_service.return_value = mock_event_service
    mock_event_service.run.return_value = None  # Successful run

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.post(f"/api/conversations/{sample_conversation_id}/run")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify services were called
        mock_conversation_service.get_event_service.assert_called_once_with(
            sample_conversation_id
        )
        mock_event_service.run.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


def test_run_conversation_not_found(
    client, mock_conversation_service, sample_conversation_id
):
    """Test run_conversation endpoint when conversation is not found."""

    # Mock the service response - conversation not found
    mock_conversation_service.get_event_service.return_value = None

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.post(f"/api/conversations/{sample_conversation_id}/run")

        assert response.status_code == 404

        # Verify service was called
        mock_conversation_service.get_event_service.assert_called_once_with(
            sample_conversation_id
        )
    finally:
        client.app.dependency_overrides.clear()


def test_run_conversation_already_running(
    client, mock_conversation_service, mock_event_service, sample_conversation_id
):
    """Test run_conversation endpoint when conversation is already running."""

    # Mock the service responses
    mock_conversation_service.get_event_service.return_value = mock_event_service
    mock_event_service.run.side_effect = ValueError("conversation_already_running")

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.post(f"/api/conversations/{sample_conversation_id}/run")

        assert response.status_code == 409  # Conflict
        data = response.json()
        assert "already running" in data["detail"]

        # Verify services were called
        mock_conversation_service.get_event_service.assert_called_once_with(
            sample_conversation_id
        )
        mock_event_service.run.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


def test_run_conversation_other_error(
    client, mock_conversation_service, mock_event_service, sample_conversation_id
):
    """Test run_conversation endpoint with other ValueError."""

    # Mock the service responses
    mock_conversation_service.get_event_service.return_value = mock_event_service
    mock_event_service.run.side_effect = ValueError("some other error")

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.post(f"/api/conversations/{sample_conversation_id}/run")

        assert response.status_code == 400  # Bad Request
        data = response.json()
        assert data["detail"] == "some other error"
    finally:
        client.app.dependency_overrides.clear()


def test_update_conversation_secrets_success(
    client, mock_conversation_service, mock_event_service, sample_conversation_id
):
    """Test update_conversation_secrets endpoint with successful update."""

    # Mock the service responses
    mock_conversation_service.get_event_service.return_value = mock_event_service
    mock_event_service.update_secrets.return_value = None

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        # Use proper secret source format
        request_data = {
            "secrets": {
                "API_KEY": {"kind": "StaticSecret", "value": "secret-value"},
                "TOKEN": {"kind": "StaticSecret", "value": "token-value"},
            }
        }

        response = client.post(
            f"/api/conversations/{sample_conversation_id}/secrets", json=request_data
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify services were called
        mock_conversation_service.get_event_service.assert_called_once_with(
            sample_conversation_id
        )
        mock_event_service.update_secrets.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


def test_update_conversation_secrets_not_found(
    client, mock_conversation_service, sample_conversation_id
):
    """Test update_conversation_secrets endpoint when conversation is not found."""

    # Mock the service response - conversation not found
    mock_conversation_service.get_event_service.return_value = None

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        request_data = {
            "secrets": {"API_KEY": {"kind": "StaticSecret", "value": "secret-value"}}
        }

        response = client.post(
            f"/api/conversations/{sample_conversation_id}/secrets", json=request_data
        )

        assert response.status_code == 404

        # Verify service was called
        mock_conversation_service.get_event_service.assert_called_once_with(
            sample_conversation_id
        )
    finally:
        client.app.dependency_overrides.clear()


def test_set_conversation_confirmation_policy_success(
    client, mock_conversation_service, mock_event_service, sample_conversation_id
):
    """Test set_conversation_confirmation_policy endpoint with successful update."""

    # Mock the service responses
    mock_conversation_service.get_event_service.return_value = mock_event_service
    mock_event_service.set_confirmation_policy.return_value = None

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        request_data = {"policy": {"kind": "NeverConfirm"}}

        response = client.post(
            f"/api/conversations/{sample_conversation_id}/confirmation_policy",
            json=request_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify services were called
        mock_conversation_service.get_event_service.assert_called_once_with(
            sample_conversation_id
        )
        mock_event_service.set_confirmation_policy.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


def test_set_conversation_confirmation_policy_not_found(
    client, mock_conversation_service, sample_conversation_id
):
    """Test set_conversation_confirmation_policy endpoint when conversation is not found."""  # noqa: E501

    # Mock the service response - conversation not found
    mock_conversation_service.get_event_service.return_value = None

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        request_data = {"policy": {"kind": "NeverConfirm"}}

        response = client.post(
            f"/api/conversations/{sample_conversation_id}/confirmation_policy",
            json=request_data,
        )

        assert response.status_code == 404

        # Verify service was called
        mock_conversation_service.get_event_service.assert_called_once_with(
            sample_conversation_id
        )
    finally:
        client.app.dependency_overrides.clear()


def test_update_conversation_success(
    client, mock_conversation_service, sample_conversation_id
):
    """Test update_conversation endpoint with successful update."""

    # Mock the service response - update successful
    mock_conversation_service.update_conversation.return_value = True

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        request_data = {"title": "Updated Conversation Title"}

        response = client.patch(
            f"/api/conversations/{sample_conversation_id}", json=request_data
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Verify service was called with correct parameters
        mock_conversation_service.update_conversation.assert_called_once()
        call_args = mock_conversation_service.update_conversation.call_args
        assert call_args[0][0] == sample_conversation_id
        assert call_args[0][1].title == "Updated Conversation Title"
    finally:
        client.app.dependency_overrides.clear()


def test_update_conversation_failure(
    client, mock_conversation_service, sample_conversation_id
):
    """Test update_conversation endpoint with update failure."""

    # Mock the service response - update failed
    mock_conversation_service.update_conversation.return_value = False

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        request_data = {"title": "Updated Title"}

        response = client.patch(
            f"/api/conversations/{sample_conversation_id}", json=request_data
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False

        # Verify service was called
        mock_conversation_service.update_conversation.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


def test_update_conversation_invalid_title(
    client, mock_conversation_service, sample_conversation_id
):
    """Test update_conversation endpoint with invalid title."""

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        # Test with empty title
        request_data = {"title": ""}
        response = client.patch(
            f"/api/conversations/{sample_conversation_id}", json=request_data
        )
        assert response.status_code == 422  # Validation error

        # Test with too long title
        long_title = "x" * 201  # Exceeds max_length=200
        request_data = {"title": long_title}
        response = client.patch(
            f"/api/conversations/{sample_conversation_id}", json=request_data
        )
        assert response.status_code == 422  # Validation error
    finally:
        client.app.dependency_overrides.clear()


def test_generate_conversation_title_success(
    client, mock_conversation_service, sample_conversation_id
):
    """Test generate_conversation_title endpoint with successful generation."""

    # Mock the service response
    mock_conversation_service.generate_conversation_title.return_value = (
        "Generated Title"
    )

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        request_data = {"max_length": 30}

        response = client.post(
            f"/api/conversations/{sample_conversation_id}/generate_title",
            json=request_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Generated Title"

        # Verify service was called with correct parameters
        mock_conversation_service.generate_conversation_title.assert_called_once()
        call_args = mock_conversation_service.generate_conversation_title.call_args
        assert call_args[0][0] == sample_conversation_id
        assert call_args[0][1] == 30  # max_length
        assert call_args[0][2] is None  # llm (default)
    finally:
        client.app.dependency_overrides.clear()


def test_generate_conversation_title_with_llm(
    client, mock_conversation_service, sample_conversation_id
):
    """Test generate_conversation_title endpoint with custom LLM."""

    # Mock the service response
    mock_conversation_service.generate_conversation_title.return_value = (
        "Custom LLM Title"
    )

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        request_data = {
            "max_length": 40,
            "llm": {
                "model": "gpt-3.5-turbo",
                "api_key": "custom-key",
                "usage_id": "custom-llm",
            },
        }

        response = client.post(
            f"/api/conversations/{sample_conversation_id}/generate_title",
            json=request_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Custom LLM Title"

        # Verify service was called
        mock_conversation_service.generate_conversation_title.assert_called_once()
        call_args = mock_conversation_service.generate_conversation_title.call_args
        assert call_args[0][0] == sample_conversation_id
        assert call_args[0][1] == 40  # max_length
        assert call_args[0][2] is not None  # llm provided
    finally:
        client.app.dependency_overrides.clear()


def test_generate_conversation_title_failure(
    client, mock_conversation_service, sample_conversation_id
):
    """Test generate_conversation_title endpoint with generation failure."""

    # Mock the service response - generation failed
    mock_conversation_service.generate_conversation_title.return_value = None

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        request_data = {"max_length": 50}

        response = client.post(
            f"/api/conversations/{sample_conversation_id}/generate_title",
            json=request_data,
        )

        assert response.status_code == 500  # Internal Server Error

        # Verify service was called
        mock_conversation_service.generate_conversation_title.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


def test_generate_conversation_title_invalid_params(
    client, mock_conversation_service, sample_conversation_id
):
    """Test generate_conversation_title endpoint with invalid parameters."""

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        # Test with max_length too low
        request_data = {"max_length": 0}
        response = client.post(
            f"/api/conversations/{sample_conversation_id}/generate_title",
            json=request_data,
        )
        assert response.status_code == 422  # Validation error

        # Test with max_length too high
        request_data = {"max_length": 201}
        response = client.post(
            f"/api/conversations/{sample_conversation_id}/generate_title",
            json=request_data,
        )
        assert response.status_code == 422  # Validation error
    finally:
        client.app.dependency_overrides.clear()


def test_start_conversation_with_tool_module_qualnames(
    client, mock_conversation_service, sample_conversation_info
):
    """Test start_conversation endpoint with tool_module_qualnames field."""

    # Mock the service response
    mock_conversation_service.start_conversation.return_value = (
        sample_conversation_info,
        True,
    )

    # Override the dependency
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        request_data = {
            "agent": {
                "llm": {
                    "model": "gpt-4o",
                    "api_key": "test-key",
                    "usage_id": "test-llm",
                },
                "tools": [
                    {"name": "glob"},
                    {"name": "grep"},
                    {"name": "planning_file_editor"},
                ],
            },
            "workspace": {"working_dir": "/tmp/test"},
            "tool_module_qualnames": {
                "glob": "openhands.tools.glob.definition",
                "grep": "openhands.tools.grep.definition",
                "planning_file_editor": (
                    "openhands.tools.planning_file_editor.definition"
                ),
            },
        }

        response = client.post("/api/conversations", json=request_data)

        assert response.status_code == 201
        data = response.json()
        assert data["id"] == str(sample_conversation_info.id)

        # Verify service was called
        mock_conversation_service.start_conversation.assert_called_once()
        call_args = mock_conversation_service.start_conversation.call_args
        request_arg = call_args[0][0]
        assert hasattr(request_arg, "tool_module_qualnames")
        assert request_arg.tool_module_qualnames == {
            "glob": "openhands.tools.glob.definition",
            "grep": "openhands.tools.grep.definition",
            "planning_file_editor": ("openhands.tools.planning_file_editor.definition"),
        }
    finally:
        client.app.dependency_overrides.clear()


def test_start_conversation_without_tool_module_qualnames(
    client, mock_conversation_service, sample_conversation_info
):
    """Test start_conversation endpoint without tool_module_qualnames field."""

    # Mock the service response
    mock_conversation_service.start_conversation.return_value = (
        sample_conversation_info,
        True,
    )

    # Override the dependency
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        request_data = {
            "agent": {
                "llm": {
                    "model": "gpt-4o",
                    "api_key": "test-key",
                    "usage_id": "test-llm",
                },
                "tools": [{"name": "TerminalTool"}],
            },
            "workspace": {"working_dir": "/tmp/test"},
        }

        response = client.post("/api/conversations", json=request_data)

        assert response.status_code == 201
        data = response.json()
        assert data["id"] == str(sample_conversation_info.id)

        # Verify service was called
        mock_conversation_service.start_conversation.assert_called_once()
        call_args = mock_conversation_service.start_conversation.call_args
        request_arg = call_args[0][0]
        assert hasattr(request_arg, "tool_module_qualnames")
        # Should default to empty dict
        assert request_arg.tool_module_qualnames == {}
    finally:
        client.app.dependency_overrides.clear()


def test_set_conversation_security_analyzer_success(
    client,
    sample_conversation_id,
    mock_conversation_service,
    mock_event_service,
    llm_security_analyzer,
):
    """Test successful setting of security analyzer via API endpoint."""
    # Setup mocks
    mock_conversation_service.get_event_service.return_value = mock_event_service
    mock_event_service.set_security_analyzer.return_value = None

    # Override dependency
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    # Make request
    response = client.post(
        f"/api/conversations/{sample_conversation_id}/security_analyzer",
        json={"security_analyzer": llm_security_analyzer.model_dump()},
    )

    # Verify response
    assert response.status_code == 200
    assert response.json() == {"success": True}

    # Verify service calls
    mock_conversation_service.get_event_service.assert_called_once_with(
        sample_conversation_id
    )
    mock_event_service.set_security_analyzer.assert_called_once()


def test_set_conversation_security_analyzer_with_none(
    client, sample_conversation_id, mock_conversation_service, mock_event_service
):
    """Test setting security analyzer to None via API endpoint."""
    # Setup mocks
    mock_conversation_service.get_event_service.return_value = mock_event_service
    mock_event_service.set_security_analyzer.return_value = None

    # Override dependency
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    # Make request with None analyzer
    response = client.post(
        f"/api/conversations/{sample_conversation_id}/security_analyzer",
        json={"security_analyzer": None},
    )

    # Verify response
    assert response.status_code == 200
    assert response.json() == {"success": True}

    # Verify service calls
    mock_conversation_service.get_event_service.assert_called_once_with(
        sample_conversation_id
    )
    mock_event_service.set_security_analyzer.assert_called_once_with(None)


def test_security_analyzer_endpoint_with_malformed_analyzer_data(
    client, sample_conversation_id, mock_conversation_service, mock_event_service
):
    """Test endpoint behavior with malformed security analyzer data."""
    # Setup mocks
    mock_conversation_service.get_event_service.return_value = mock_event_service
    mock_event_service.set_security_analyzer.return_value = None

    # Override dependency
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    # Test with invalid analyzer type (should be rejected)
    response = client.post(
        f"/api/conversations/{sample_conversation_id}/security_analyzer",
        json={"security_analyzer": {"kind": "InvalidAnalyzerType"}},
    )

    # Should return validation error for unknown analyzer type
    assert response.status_code == 422
    response_data = response.json()
    assert "detail" in response_data


def test_update_secrets_with_string_values(
    client, mock_conversation_service, mock_event_service, sample_conversation_id
):
    """Test update_secrets endpoint accepts plain string values."""

    # Mock the services
    mock_conversation_service.get_event_service.return_value = mock_event_service
    mock_event_service.update_secrets.return_value = None

    # Override dependency
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        # Test with plain string secrets (should be auto-converted)
        response = client.post(
            f"/api/conversations/{sample_conversation_id}/secrets",
            json={
                "secrets": {
                    "API_KEY": "plain-secret-value",
                    "TOKEN": "another-secret",
                }
            },
        )

        assert response.status_code == 200
        assert response.json() == {"success": True}

        # Verify the event service was called (secrets should be converted internally)
        mock_event_service.update_secrets.assert_called_once()
        call_args = mock_event_service.update_secrets.call_args

        # Verify secrets were converted to proper SecretSource objects
        secrets_dict = call_args[0][0]  # secrets parameter
        assert "API_KEY" in secrets_dict
        assert "TOKEN" in secrets_dict

    finally:
        client.app.dependency_overrides.clear()


def test_update_secrets_with_mixed_formats(
    client, mock_conversation_service, mock_event_service, sample_conversation_id
):
    """Test update_secrets endpoint accepts mixed secret formats."""

    # Mock the services
    mock_conversation_service.get_event_service.return_value = mock_event_service
    mock_event_service.update_secrets.return_value = None

    # Override dependency
    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        # Test with mixed formats: plain strings and proper SecretSource objects
        response = client.post(
            f"/api/conversations/{sample_conversation_id}/secrets",
            json={
                "secrets": {
                    "PLAIN_SECRET": "plain-value",
                    "STATIC_SECRET": {
                        "kind": "StaticSecret",
                        "value": "static-value",
                    },
                    "LOOKUP_SECRET": {
                        "kind": "LookupSecret",
                        "url": "https://example.com/secret",
                    },
                }
            },
        )

        assert response.status_code == 200
        assert response.json() == {"success": True}

        # Verify the event service was called
        mock_event_service.update_secrets.assert_called_once()
        call_args = mock_event_service.update_secrets.call_args

        # Verify all secrets are present
        secrets_dict = call_args[0][0]  # secrets parameter
        assert "PLAIN_SECRET" in secrets_dict
        assert "STATIC_SECRET" in secrets_dict
        assert "LOOKUP_SECRET" in secrets_dict

    finally:
        client.app.dependency_overrides.clear()
