import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import (
    ConversationPage,
    ConversationSortOrder,
    StartConversationRequest,
    StoredConversation,
    UpdateConversationRequest,
)
from openhands.agent_server.utils import safe_rmtree as _safe_rmtree
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.secret import SecretSource, StaticSecret
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture
def mock_event_service():
    """Create a mock EventService with stored conversation data."""
    service = AsyncMock(spec=EventService)
    return service


@pytest.fixture
def sample_stored_conversation():
    """Create a sample StoredConversation for testing."""
    return StoredConversation(
        id=uuid4(),
        agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir="workspace/project"),
        confirmation_policy=NeverConfirm(),
        initial_message=None,
        metrics=None,
        created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
        updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
    )


@pytest.fixture
def conversation_service():
    """Create a ConversationService instance for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        service = ConversationService(
            conversations_dir=Path(temp_dir) / "conversations",
        )
        # Initialize the _event_services dict to simulate an active service
        service._event_services = {}
        yield service


class TestConversationServiceSearchConversations:
    """Test cases for ConversationService.search_conversations method."""

    @pytest.mark.asyncio
    async def test_search_conversations_inactive_service(self, conversation_service):
        """Test that search_conversations raises ValueError when service is inactive."""
        conversation_service._event_services = None

        with pytest.raises(ValueError, match="inactive_service"):
            await conversation_service.search_conversations()

    @pytest.mark.asyncio
    async def test_search_conversations_empty_result(self, conversation_service):
        """Test search_conversations with no conversations."""
        result = await conversation_service.search_conversations()

        assert isinstance(result, ConversationPage)
        assert result.items == []
        assert result.next_page_id is None

    @pytest.mark.asyncio
    async def test_search_conversations_basic(
        self, conversation_service, sample_stored_conversation
    ):
        """Test basic search_conversations functionality."""
        # Create mock event service
        mock_service = AsyncMock(spec=EventService)
        mock_service.stored = sample_stored_conversation
        mock_state = ConversationState(
            id=sample_stored_conversation.id,
            agent=sample_stored_conversation.agent,
            workspace=sample_stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=sample_stored_conversation.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        conversation_id = sample_stored_conversation.id
        conversation_service._event_services[conversation_id] = mock_service

        result = await conversation_service.search_conversations()

        assert len(result.items) == 1
        assert result.items[0].id == conversation_id
        assert result.items[0].execution_status == ConversationExecutionStatus.IDLE
        assert result.next_page_id is None

    @pytest.mark.asyncio
    async def test_search_conversations_status_filter(self, conversation_service):
        """Test filtering conversations by status."""
        # Create multiple conversations with different statuses
        conversations = []
        for i, status in enumerate(
            [
                ConversationExecutionStatus.IDLE,
                ConversationExecutionStatus.RUNNING,
                ConversationExecutionStatus.FINISHED,
            ]
        ):
            stored_conv = StoredConversation(
                id=uuid4(),
                agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir="workspace/project"),
                confirmation_policy=NeverConfirm(),
                initial_message=None,
                metrics=None,
                created_at=datetime(2025, 1, 1, 12, i, 0, tzinfo=UTC),
                updated_at=datetime(2025, 1, 1, 12, i + 30, 0, tzinfo=UTC),
            )

            mock_service = AsyncMock(spec=EventService)
            mock_service.stored = stored_conv
            mock_state = ConversationState(
                id=stored_conv.id,
                agent=stored_conv.agent,
                workspace=stored_conv.workspace,
                execution_status=status,
                confirmation_policy=stored_conv.confirmation_policy,
            )
            mock_service.get_state.return_value = mock_state

            conversation_service._event_services[stored_conv.id] = mock_service
            conversations.append((stored_conv.id, status))

        # Test filtering by IDLE status
        result = await conversation_service.search_conversations(
            execution_status=ConversationExecutionStatus.IDLE
        )
        assert len(result.items) == 1
        assert result.items[0].execution_status == ConversationExecutionStatus.IDLE

        # Test filtering by RUNNING status
        result = await conversation_service.search_conversations(
            execution_status=ConversationExecutionStatus.RUNNING
        )
        assert len(result.items) == 1
        assert result.items[0].execution_status == ConversationExecutionStatus.RUNNING

        # Test filtering by non-existent status
        result = await conversation_service.search_conversations(
            execution_status=ConversationExecutionStatus.ERROR
        )
        assert len(result.items) == 0

    @pytest.mark.asyncio
    async def test_search_conversations_sorting(self, conversation_service):
        """Test sorting conversations by different criteria."""
        # Create conversations with different timestamps
        conversations = []

        for i in range(3):
            stored_conv = StoredConversation(
                id=uuid4(),
                agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir="workspace/project"),
                confirmation_policy=NeverConfirm(),
                initial_message=None,
                metrics=None,
                created_at=datetime(
                    2025, 1, i + 1, 12, 0, 0, tzinfo=UTC
                ),  # Different days
                updated_at=datetime(2025, 1, i + 1, 12, 30, 0, tzinfo=UTC),
            )

            mock_service = AsyncMock(spec=EventService)
            mock_service.stored = stored_conv
            mock_state = ConversationState(
                id=stored_conv.id,
                agent=stored_conv.agent,
                workspace=stored_conv.workspace,
                execution_status=ConversationExecutionStatus.IDLE,
                confirmation_policy=stored_conv.confirmation_policy,
            )
            mock_service.get_state.return_value = mock_state

            conversation_service._event_services[stored_conv.id] = mock_service
            conversations.append(stored_conv)

        # Test CREATED_AT (ascending)
        result = await conversation_service.search_conversations(
            sort_order=ConversationSortOrder.CREATED_AT
        )
        assert len(result.items) == 3
        assert (
            result.items[0].created_at
            < result.items[1].created_at
            < result.items[2].created_at
        )

        # Test CREATED_AT_DESC (descending) - default
        result = await conversation_service.search_conversations(
            sort_order=ConversationSortOrder.CREATED_AT_DESC
        )
        assert len(result.items) == 3
        assert (
            result.items[0].created_at
            > result.items[1].created_at
            > result.items[2].created_at
        )

        # Test UPDATED_AT (ascending)
        result = await conversation_service.search_conversations(
            sort_order=ConversationSortOrder.UPDATED_AT
        )
        assert len(result.items) == 3
        assert (
            result.items[0].updated_at
            < result.items[1].updated_at
            < result.items[2].updated_at
        )

        # Test UPDATED_AT_DESC (descending)
        result = await conversation_service.search_conversations(
            sort_order=ConversationSortOrder.UPDATED_AT_DESC
        )
        assert len(result.items) == 3
        assert (
            result.items[0].updated_at
            > result.items[1].updated_at
            > result.items[2].updated_at
        )

    @pytest.mark.asyncio
    async def test_search_conversations_pagination(self, conversation_service):
        """Test pagination functionality."""
        # Create 5 conversations
        conversation_ids = []
        for i in range(5):
            stored_conv = StoredConversation(
                id=uuid4(),
                agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir="workspace/project"),
                confirmation_policy=NeverConfirm(),
                initial_message=None,
                metrics=None,
                created_at=datetime(2025, 1, 1, 12, i, 0, tzinfo=UTC),
                updated_at=datetime(2025, 1, 1, 12, i + 30, 0, tzinfo=UTC),
            )

            mock_service = AsyncMock(spec=EventService)
            mock_service.stored = stored_conv
            mock_state = ConversationState(
                id=stored_conv.id,
                agent=stored_conv.agent,
                workspace=stored_conv.workspace,
                execution_status=ConversationExecutionStatus.IDLE,
                confirmation_policy=stored_conv.confirmation_policy,
            )
            mock_service.get_state.return_value = mock_state

            conversation_service._event_services[stored_conv.id] = mock_service
            conversation_ids.append(stored_conv.id)

        # Test first page with limit 2
        result = await conversation_service.search_conversations(limit=2)
        assert len(result.items) == 2
        assert result.next_page_id is not None

        # Test second page using next_page_id
        result = await conversation_service.search_conversations(
            page_id=result.next_page_id, limit=2
        )
        assert len(result.items) == 2
        assert result.next_page_id is not None

        # Test last page
        result = await conversation_service.search_conversations(
            page_id=result.next_page_id, limit=2
        )
        assert len(result.items) == 1  # Only one item left
        assert result.next_page_id is None

    @pytest.mark.asyncio
    async def test_search_conversations_combined_filter_and_sort(
        self, conversation_service
    ):
        """Test combining status filtering with sorting."""
        # Create conversations with mixed statuses and timestamps
        conversations_data = [
            (
                ConversationExecutionStatus.IDLE,
                datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
            ),
            (
                ConversationExecutionStatus.RUNNING,
                datetime(2025, 1, 2, 12, 0, 0, tzinfo=UTC),
            ),
            (
                ConversationExecutionStatus.IDLE,
                datetime(2025, 1, 3, 12, 0, 0, tzinfo=UTC),
            ),
            (
                ConversationExecutionStatus.FINISHED,
                datetime(2025, 1, 4, 12, 0, 0, tzinfo=UTC),
            ),
        ]

        for status, created_at in conversations_data:
            stored_conv = StoredConversation(
                id=uuid4(),
                agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir="workspace/project"),
                confirmation_policy=NeverConfirm(),
                initial_message=None,
                metrics=None,
                created_at=created_at,
                updated_at=created_at,
            )

            mock_service = AsyncMock(spec=EventService)
            mock_service.stored = stored_conv
            mock_state = ConversationState(
                id=stored_conv.id,
                agent=stored_conv.agent,
                workspace=stored_conv.workspace,
                execution_status=status,
                confirmation_policy=stored_conv.confirmation_policy,
            )
            mock_service.get_state.return_value = mock_state

            conversation_service._event_services[stored_conv.id] = mock_service

        # Filter by IDLE status and sort by CREATED_AT_DESC
        result = await conversation_service.search_conversations(
            execution_status=ConversationExecutionStatus.IDLE,
            sort_order=ConversationSortOrder.CREATED_AT_DESC,
        )

        assert len(result.items) == 2  # Two IDLE conversations
        # Should be sorted by created_at descending (newest first)
        assert result.items[0].created_at > result.items[1].created_at

    @pytest.mark.asyncio
    async def test_search_conversations_invalid_page_id(
        self, conversation_service, sample_stored_conversation
    ):
        """Test search_conversations with invalid page_id."""
        mock_service = AsyncMock(spec=EventService)
        mock_service.stored = sample_stored_conversation
        mock_state = ConversationState(
            id=sample_stored_conversation.id,
            agent=sample_stored_conversation.agent,
            workspace=sample_stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=sample_stored_conversation.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        conversation_service._event_services[sample_stored_conversation.id] = (
            mock_service
        )

        # Use a non-existent page_id
        invalid_page_id = uuid4().hex
        result = await conversation_service.search_conversations(
            page_id=invalid_page_id
        )

        # Should return all items since page_id doesn't match any conversation
        assert len(result.items) == 1
        assert result.next_page_id is None


class TestConversationServiceCountConversations:
    """Test cases for ConversationService.count_conversations method."""

    @pytest.mark.asyncio
    async def test_count_conversations_inactive_service(self, conversation_service):
        """Test that count_conversations raises ValueError when service is inactive."""
        conversation_service._event_services = None

        with pytest.raises(ValueError, match="inactive_service"):
            await conversation_service.count_conversations()

    @pytest.mark.asyncio
    async def test_count_conversations_empty_result(self, conversation_service):
        """Test count_conversations with no conversations."""
        result = await conversation_service.count_conversations()
        assert result == 0

    @pytest.mark.asyncio
    async def test_count_conversations_basic(
        self, conversation_service, sample_stored_conversation
    ):
        """Test basic count_conversations functionality."""
        # Create mock event service
        mock_service = AsyncMock(spec=EventService)
        mock_service.stored = sample_stored_conversation
        mock_state = ConversationState(
            id=sample_stored_conversation.id,
            agent=sample_stored_conversation.agent,
            workspace=sample_stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=sample_stored_conversation.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        conversation_id = sample_stored_conversation.id
        conversation_service._event_services[conversation_id] = mock_service

        result = await conversation_service.count_conversations()
        assert result == 1

    @pytest.mark.asyncio
    async def test_count_conversations_status_filter(self, conversation_service):
        """Test counting conversations with status filter."""
        # Create multiple conversations with different statuses
        statuses = [
            ConversationExecutionStatus.IDLE,
            ConversationExecutionStatus.RUNNING,
            ConversationExecutionStatus.FINISHED,
            ConversationExecutionStatus.IDLE,  # Another IDLE one
        ]

        for i, status in enumerate(statuses):
            stored_conv = StoredConversation(
                id=uuid4(),
                agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir="workspace/project"),
                confirmation_policy=NeverConfirm(),
                initial_message=None,
                metrics=None,
                created_at=datetime(2025, 1, 1, 12, i, 0, tzinfo=UTC),
                updated_at=datetime(2025, 1, 1, 12, i + 30, 0, tzinfo=UTC),
            )

            mock_service = AsyncMock(spec=EventService)
            mock_service.stored = stored_conv
            mock_state = ConversationState(
                id=stored_conv.id,
                agent=stored_conv.agent,
                workspace=stored_conv.workspace,
                execution_status=status,
                confirmation_policy=stored_conv.confirmation_policy,
            )
            mock_service.get_state.return_value = mock_state

            conversation_service._event_services[stored_conv.id] = mock_service

        # Test counting all conversations
        result = await conversation_service.count_conversations()
        assert result == 4

        # Test counting by IDLE status (should be 2)
        result = await conversation_service.count_conversations(
            execution_status=ConversationExecutionStatus.IDLE
        )
        assert result == 2

        # Test counting by RUNNING status (should be 1)
        result = await conversation_service.count_conversations(
            execution_status=ConversationExecutionStatus.RUNNING
        )
        assert result == 1

        # Test counting by non-existent status (should be 0)
        result = await conversation_service.count_conversations(
            execution_status=ConversationExecutionStatus.ERROR
        )
        assert result == 0


class TestConversationServiceStartConversation:
    """Test cases for ConversationService.start_conversation method."""

    @pytest.mark.asyncio
    async def test_start_conversation_with_secrets(self, conversation_service):
        """Test that secrets are passed to new conversations when starting."""
        # Create test secrets
        test_secrets: dict[str, SecretSource] = {
            "api_key": StaticSecret(value=SecretStr("secret-api-key-123")),
            "database_url": StaticSecret(
                value=SecretStr("postgresql://user:pass@host:5432/db")
            ),
        }

        # Create a start conversation request with secrets
        with tempfile.TemporaryDirectory() as temp_dir:
            request = StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                confirmation_policy=NeverConfirm(),
                secrets=test_secrets,
            )

            # Mock the EventService constructor and start method
            with patch(
                "openhands.agent_server.conversation_service.EventService"
            ) as mock_event_service_class:
                mock_event_service = AsyncMock(spec=EventService)
                mock_event_service_class.return_value = mock_event_service

                # Mock the state that would be returned
                mock_state = ConversationState(
                    id=uuid4(),
                    agent=request.agent,
                    workspace=request.workspace,
                    execution_status=ConversationExecutionStatus.IDLE,
                    confirmation_policy=request.confirmation_policy,
                )
                mock_event_service.get_state.return_value = mock_state
                mock_event_service.stored = StoredConversation(
                    id=mock_state.id,
                    **request.model_dump(),
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )

                # Start the conversation
                result, _ = await conversation_service.start_conversation(request)

                # Verify EventService was created with the correct parameters
                mock_event_service_class.assert_called_once()
                call_args = mock_event_service_class.call_args
                stored_conversation = call_args.kwargs["stored"]

                # Verify that secrets were passed to the stored conversation
                assert stored_conversation.secrets == test_secrets
                assert "api_key" in stored_conversation.secrets
                assert "database_url" in stored_conversation.secrets
                assert (
                    stored_conversation.secrets["api_key"].get_value()
                    == "secret-api-key-123"
                )
                assert (
                    stored_conversation.secrets["database_url"].get_value()
                    == "postgresql://user:pass@host:5432/db"
                )

                # Verify the conversation was started
                mock_event_service.start.assert_called_once()

                # Verify the result
                assert result.id == mock_state.id
                assert result.execution_status == ConversationExecutionStatus.IDLE

    @pytest.mark.asyncio
    async def test_start_conversation_without_secrets(self, conversation_service):
        """Test that conversations can be started without secrets."""
        # Create a start conversation request without secrets
        with tempfile.TemporaryDirectory() as temp_dir:
            request = StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                confirmation_policy=NeverConfirm(),
            )

            # Mock the EventService constructor and start method
            with patch(
                "openhands.agent_server.conversation_service.EventService"
            ) as mock_event_service_class:
                mock_event_service = AsyncMock(spec=EventService)
                mock_event_service_class.return_value = mock_event_service

                # Mock the state that would be returned
                mock_state = ConversationState(
                    id=uuid4(),
                    agent=request.agent,
                    workspace=request.workspace,
                    execution_status=ConversationExecutionStatus.IDLE,
                    confirmation_policy=request.confirmation_policy,
                )
                mock_event_service.get_state.return_value = mock_state
                mock_event_service.stored = StoredConversation(
                    id=mock_state.id,
                    **request.model_dump(),
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )

                # Start the conversation
                result, _ = await conversation_service.start_conversation(request)

                # Verify EventService was created with the correct parameters
                mock_event_service_class.assert_called_once()
                call_args = mock_event_service_class.call_args
                stored_conversation = call_args.kwargs["stored"]

                # Verify that secrets is an empty dict (default)
                assert stored_conversation.secrets == {}

                # Verify the conversation was started
                mock_event_service.start.assert_called_once()

                # Verify the result
                assert result.id == mock_state.id
                assert result.execution_status == ConversationExecutionStatus.IDLE

    @pytest.mark.asyncio
    async def test_start_conversation_with_custom_id(self, conversation_service):
        """Test that conversations can be started with a custom conversation_id."""
        custom_id = uuid4()

        # Create a start conversation request with custom conversation_id
        with tempfile.TemporaryDirectory() as temp_dir:
            request = StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                confirmation_policy=NeverConfirm(),
                conversation_id=custom_id,
            )

            result, is_new = await conversation_service.start_conversation(request)
            assert result.id == custom_id
            assert is_new

    @pytest.mark.asyncio
    async def test_start_conversation_with_duplicate_id(self, conversation_service):
        """Test duplicate conversation ids are detected."""
        custom_id = uuid4()

        # Create a start conversation request with custom conversation_id
        with tempfile.TemporaryDirectory() as temp_dir:
            request = StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                confirmation_policy=NeverConfirm(),
                conversation_id=custom_id,
            )

            result, is_new = await conversation_service.start_conversation(request)
            assert result.id == custom_id
            assert is_new

            duplicate_request = StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                confirmation_policy=NeverConfirm(),
                conversation_id=custom_id,
            )

            result, is_new = await conversation_service.start_conversation(
                duplicate_request
            )
            assert result.id == custom_id
            assert not is_new

    @pytest.mark.asyncio
    async def test_start_conversation_reuse_checks_is_open(self, conversation_service):
        """Test that conversation reuse checks if event service is open."""
        custom_id = uuid4()

        # Create a mock event service that exists but is not open
        mock_event_service = AsyncMock(spec=EventService)
        mock_event_service.is_open.return_value = False
        conversation_service._event_services[custom_id] = mock_event_service

        with tempfile.TemporaryDirectory() as temp_dir:
            request = StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                confirmation_policy=NeverConfirm(),
                conversation_id=custom_id,
            )

            # Mock the _start_event_service method to avoid actual startup
            with patch.object(
                conversation_service, "_start_event_service"
            ) as mock_start:
                mock_new_service = AsyncMock(spec=EventService)
                mock_new_service.stored = StoredConversation(
                    id=custom_id,
                    agent=request.agent,
                    workspace=request.workspace,
                    confirmation_policy=request.confirmation_policy,
                    initial_message=request.initial_message,
                    metrics=None,
                    created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
                    updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
                )
                mock_state = ConversationState(
                    id=custom_id,
                    agent=request.agent,
                    workspace=request.workspace,
                    execution_status=ConversationExecutionStatus.IDLE,
                    confirmation_policy=request.confirmation_policy,
                )
                mock_new_service.get_state.return_value = mock_state
                mock_start.return_value = mock_new_service

                result, is_new = await conversation_service.start_conversation(request)

                # Should create a new conversation since existing one is not open
                assert result.id == custom_id
                assert is_new
                mock_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_conversation_reuse_when_open(self, conversation_service):
        """Test that conversation is reused when event service is open."""
        custom_id = uuid4()

        # Create a mock event service that exists and is open
        mock_event_service = AsyncMock(spec=EventService)
        mock_event_service.is_open.return_value = True
        mock_event_service.stored = StoredConversation(
            id=custom_id,
            agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="workspace/project"),
            confirmation_policy=NeverConfirm(),
            initial_message=None,
            metrics=None,
            created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
        )
        mock_state = ConversationState(
            id=custom_id,
            agent=mock_event_service.stored.agent,
            workspace=mock_event_service.stored.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=mock_event_service.stored.confirmation_policy,
        )
        mock_event_service.get_state.return_value = mock_state
        conversation_service._event_services[custom_id] = mock_event_service

        with tempfile.TemporaryDirectory() as temp_dir:
            request = StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                confirmation_policy=NeverConfirm(),
                conversation_id=custom_id,
            )

            # Mock the _start_event_service method to ensure it's not called
            with patch.object(
                conversation_service, "_start_event_service"
            ) as mock_start:
                result, is_new = await conversation_service.start_conversation(request)

                # Should reuse existing conversation since it's open
                assert result.id == custom_id
                assert not is_new
                mock_start.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_event_service_failure_cleanup(self, conversation_service):
        """Test that event service is cleaned up when startup fails."""
        with tempfile.TemporaryDirectory() as temp_dir:
            stored = StoredConversation(
                id=uuid4(),
                agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                confirmation_policy=NeverConfirm(),
                initial_message=None,
                metrics=None,
                created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
                updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
            )

            # Mock EventService to simulate startup failure
            with patch(
                "openhands.agent_server.conversation_service.EventService"
            ) as mock_event_service_class:
                mock_event_service = AsyncMock()
                mock_event_service.start.side_effect = Exception("Startup failed")
                mock_event_service.close = AsyncMock()
                mock_event_service_class.return_value = mock_event_service

                # Attempt to start event service should fail and clean up
                with pytest.raises(Exception, match="Startup failed"):
                    await conversation_service._start_event_service(stored)

                # Verify cleanup was called
                mock_event_service.close.assert_called_once()

                # Verify event service was not stored
                assert stored.id not in conversation_service._event_services

    @pytest.mark.asyncio
    async def test_start_event_service_success_stores_service(
        self, conversation_service
    ):
        """Test that event service is stored only after successful startup."""
        with tempfile.TemporaryDirectory() as temp_dir:
            stored = StoredConversation(
                id=uuid4(),
                agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                confirmation_policy=NeverConfirm(),
                initial_message=None,
                metrics=None,
                created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
                updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
            )

            # Mock EventService to simulate successful startup
            with patch(
                "openhands.agent_server.conversation_service.EventService"
            ) as mock_event_service_class:
                mock_event_service = AsyncMock()
                mock_event_service.start = AsyncMock()  # Successful startup
                mock_event_service_class.return_value = mock_event_service

                # Start event service should succeed
                result = await conversation_service._start_event_service(stored)

                # Verify startup was called
                mock_event_service.start.assert_called_once()

                # Verify event service was stored after successful startup
                assert stored.id in conversation_service._event_services
                assert (
                    conversation_service._event_services[stored.id]
                    == mock_event_service
                )
                assert result == mock_event_service


class TestConversationServiceUpdateConversation:
    """Test cases for ConversationService.update_conversation method."""

    @pytest.mark.asyncio
    async def test_update_conversation_success(
        self, conversation_service, sample_stored_conversation
    ):
        """Test successful update of conversation title."""
        # Create mock event service
        mock_service = AsyncMock(spec=EventService)
        mock_service.stored = sample_stored_conversation
        mock_state = ConversationState(
            id=sample_stored_conversation.id,
            agent=sample_stored_conversation.agent,
            workspace=sample_stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=sample_stored_conversation.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        conversation_id = sample_stored_conversation.id
        conversation_service._event_services[conversation_id] = mock_service

        # Update the title
        new_title = "My Updated Conversation Title"
        request = UpdateConversationRequest(title=new_title)
        result = await conversation_service.update_conversation(
            conversation_id, request
        )

        # Verify update was successful
        assert result is True
        assert mock_service.stored.title == new_title
        mock_service.save_meta.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_conversation_strips_whitespace(
        self, conversation_service, sample_stored_conversation
    ):
        """Test that update_conversation strips leading/trailing whitespace."""
        mock_service = AsyncMock(spec=EventService)
        mock_service.stored = sample_stored_conversation
        mock_state = ConversationState(
            id=sample_stored_conversation.id,
            agent=sample_stored_conversation.agent,
            workspace=sample_stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=sample_stored_conversation.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        conversation_id = sample_stored_conversation.id
        conversation_service._event_services[conversation_id] = mock_service

        # Update with title that has whitespace
        new_title = "   Whitespace Test   "
        request = UpdateConversationRequest(title=new_title)
        result = await conversation_service.update_conversation(
            conversation_id, request
        )

        # Verify whitespace was stripped
        assert result is True
        assert mock_service.stored.title == "Whitespace Test"
        mock_service.save_meta.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_conversation_not_found(self, conversation_service):
        """Test updating a non-existent conversation returns False."""
        non_existent_id = uuid4()
        request = UpdateConversationRequest(title="New Title")
        result = await conversation_service.update_conversation(
            non_existent_id, request
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_update_conversation_inactive_service(self, conversation_service):
        """Test that update_conversation raises ValueError when service is inactive."""
        conversation_service._event_services = None

        request = UpdateConversationRequest(title="New Title")
        with pytest.raises(ValueError, match="inactive_service"):
            await conversation_service.update_conversation(uuid4(), request)

    @pytest.mark.asyncio
    async def test_update_conversation_notifies_webhooks(
        self, conversation_service, sample_stored_conversation
    ):
        """Test that updating a conversation triggers webhook notifications."""
        # Create mock event service
        mock_service = AsyncMock(spec=EventService)
        mock_service.stored = sample_stored_conversation
        mock_state = ConversationState(
            id=sample_stored_conversation.id,
            agent=sample_stored_conversation.agent,
            workspace=sample_stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=sample_stored_conversation.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        conversation_id = sample_stored_conversation.id
        conversation_service._event_services[conversation_id] = mock_service

        # Mock webhook notification
        with patch.object(
            conversation_service, "_notify_conversation_webhooks", new=AsyncMock()
        ) as mock_notify:
            new_title = "Updated Title for Webhook Test"
            request = UpdateConversationRequest(title=new_title)
            result = await conversation_service.update_conversation(
                conversation_id, request
            )

            # Verify webhook was called
            assert result is True
            mock_notify.assert_called_once()
            # Verify the conversation info passed to webhook has the updated title
            call_args = mock_notify.call_args[0]
            conversation_info = call_args[0]
            assert conversation_info.title == new_title

    @pytest.mark.asyncio
    async def test_update_conversation_persists_changes(
        self, conversation_service, sample_stored_conversation
    ):
        """Test that title changes are persisted to disk."""
        mock_service = AsyncMock(spec=EventService)
        mock_service.stored = sample_stored_conversation
        mock_state = ConversationState(
            id=sample_stored_conversation.id,
            agent=sample_stored_conversation.agent,
            workspace=sample_stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=sample_stored_conversation.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        conversation_id = sample_stored_conversation.id
        conversation_service._event_services[conversation_id] = mock_service

        # Initial title should be None
        assert mock_service.stored.title is None

        # Update the title
        new_title = "Persisted Title"
        request = UpdateConversationRequest(title=new_title)
        await conversation_service.update_conversation(conversation_id, request)

        # Verify save_meta was called to persist changes
        mock_service.save_meta.assert_called_once()
        # Verify the stored conversation has the new title
        assert mock_service.stored.title == new_title

    @pytest.mark.asyncio
    async def test_update_conversation_multiple_times(
        self, conversation_service, sample_stored_conversation
    ):
        """Test updating the same conversation multiple times."""
        mock_service = AsyncMock(spec=EventService)
        mock_service.stored = sample_stored_conversation
        mock_state = ConversationState(
            id=sample_stored_conversation.id,
            agent=sample_stored_conversation.agent,
            workspace=sample_stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=sample_stored_conversation.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        conversation_id = sample_stored_conversation.id
        conversation_service._event_services[conversation_id] = mock_service

        # First update
        request1 = UpdateConversationRequest(title="First Title")
        result1 = await conversation_service.update_conversation(
            conversation_id, request1
        )
        assert result1 is True
        assert mock_service.stored.title == "First Title"

        # Second update
        request2 = UpdateConversationRequest(title="Second Title")
        result2 = await conversation_service.update_conversation(
            conversation_id, request2
        )
        assert result2 is True
        assert mock_service.stored.title == "Second Title"

        # Third update
        request3 = UpdateConversationRequest(title="Third Title")
        result3 = await conversation_service.update_conversation(
            conversation_id, request3
        )
        assert result3 is True
        assert mock_service.stored.title == "Third Title"

        # Verify save_meta was called three times
        assert mock_service.save_meta.call_count == 3


class TestConversationServiceDeleteConversation:
    """Test cases for ConversationService.delete_conversation method."""

    @pytest.mark.asyncio
    async def test_delete_conversation_inactive_service(self, conversation_service):
        """Test that delete_conversation raises ValueError when service is inactive."""
        conversation_service._event_services = None

        with pytest.raises(ValueError, match="inactive_service"):
            await conversation_service.delete_conversation(uuid4())

    @pytest.mark.asyncio
    async def test_delete_conversation_not_found(self, conversation_service):
        """Test delete_conversation with non-existent conversation ID."""
        result = await conversation_service.delete_conversation(uuid4())
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_conversation_success(self, conversation_service):
        """Test successful conversation deletion."""
        conversation_id = uuid4()

        # Create mock event service
        mock_service = AsyncMock(spec=EventService)
        mock_service.conversation_dir = "/tmp/test_conversation"
        mock_service.stored = StoredConversation(
            id=conversation_id,
            agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="/tmp/test_workspace"),
            confirmation_policy=NeverConfirm(),
            initial_message=None,
            metrics=None,
            created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
        )
        mock_state = ConversationState(
            id=conversation_id,
            agent=mock_service.stored.agent,
            workspace=mock_service.stored.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=mock_service.stored.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        # Add to service
        conversation_service._event_services[conversation_id] = mock_service

        # Mock the directory removal to avoid actual filesystem operations
        with patch(
            "openhands.agent_server.conversation_service.safe_rmtree"
        ) as mock_rmtree:
            mock_rmtree.return_value = True

            result = await conversation_service.delete_conversation(conversation_id)

            assert result is True
            assert conversation_id not in conversation_service._event_services

            # Verify event service was closed
            mock_service.close.assert_called_once()

            # Verify directories were removed
            assert mock_rmtree.call_count == 1
            mock_rmtree.assert_any_call(
                "/tmp/test_conversation",
                "conversation directory for " + str(conversation_id),
            )

    @pytest.mark.asyncio
    async def test_delete_conversation_notifies_webhooks_with_deleting_status(
        self, conversation_service, sample_stored_conversation
    ):
        """Test that deleting a conversation triggers webhook notifications.

        Verifies that the webhook receives a conversation info with execution_status
        set to 'deleting' when delete_conversation is called.
        """
        # Create mock event service
        mock_service = AsyncMock(spec=EventService)
        mock_service.conversation_dir = "/tmp/test_conversation"
        mock_service.stored = sample_stored_conversation
        mock_state = ConversationState(
            id=sample_stored_conversation.id,
            agent=sample_stored_conversation.agent,
            workspace=sample_stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=sample_stored_conversation.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        conversation_id = sample_stored_conversation.id
        conversation_service._event_services[conversation_id] = mock_service

        # Mock webhook notification
        with patch.object(
            conversation_service, "_notify_conversation_webhooks", new=AsyncMock()
        ) as mock_notify:
            # Mock the directory removal
            with patch(
                "openhands.agent_server.conversation_service.safe_rmtree"
            ) as mock_rmtree:
                mock_rmtree.return_value = True

                result = await conversation_service.delete_conversation(conversation_id)

                # Verify deletion succeeded
                assert result is True
                assert conversation_id not in conversation_service._event_services

                # Verify webhook was called
                mock_notify.assert_called_once()

                # Verify the conversation info passed to webhook has 'deleting' status
                call_args = mock_notify.call_args[0]
                conversation_info = call_args[0]
                assert (
                    conversation_info.execution_status
                    == ConversationExecutionStatus.DELETING
                )

                # Verify event service was closed
                mock_service.close.assert_called_once()

                # Verify directories were removed
                assert mock_rmtree.call_count == 1

    @pytest.mark.asyncio
    async def test_delete_conversation_webhook_failure(self, conversation_service):
        """Test delete_conversation continues when webhook notification fails."""
        conversation_id = uuid4()

        # Create mock event service
        mock_service = AsyncMock(spec=EventService)
        mock_service.conversation_dir = "/tmp/test_conversation"
        mock_service.stored = StoredConversation(
            id=conversation_id,
            agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="/tmp/test_workspace"),
            confirmation_policy=NeverConfirm(),
            initial_message=None,
            metrics=None,
            created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
        )

        # Make get_state raise an exception to simulate webhook failure
        mock_service.get_state.side_effect = Exception("Webhook notification failed")

        # Add to service
        conversation_service._event_services[conversation_id] = mock_service

        # Mock the directory removal
        with patch(
            "openhands.agent_server.conversation_service.safe_rmtree"
        ) as mock_rmtree:
            mock_rmtree.return_value = True

            result = await conversation_service.delete_conversation(conversation_id)

            # Should still succeed despite webhook failure
            assert result is True
            assert conversation_id not in conversation_service._event_services

            # Verify event service was still closed
            mock_service.close.assert_called_once()

            # Verify directories were still removed
            assert mock_rmtree.call_count == 1

    @pytest.mark.asyncio
    async def test_delete_conversation_close_failure(self, conversation_service):
        """Test delete_conversation continues when event service close fails."""
        conversation_id = uuid4()

        # Create mock event service
        mock_service = AsyncMock(spec=EventService)
        mock_service.conversation_dir = "/tmp/test_conversation"
        mock_service.stored = StoredConversation(
            id=conversation_id,
            agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="/tmp/test_workspace"),
            confirmation_policy=NeverConfirm(),
            initial_message=None,
            metrics=None,
            created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
        )
        mock_state = ConversationState(
            id=conversation_id,
            agent=mock_service.stored.agent,
            workspace=mock_service.stored.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=mock_service.stored.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        # Make close raise an exception
        mock_service.close.side_effect = Exception("Close failed")

        # Add to service
        conversation_service._event_services[conversation_id] = mock_service

        # Mock the directory removal
        with patch(
            "openhands.agent_server.conversation_service.safe_rmtree"
        ) as mock_rmtree:
            mock_rmtree.return_value = True

            result = await conversation_service.delete_conversation(conversation_id)

            # Should still succeed despite close failure
            assert result is True
            assert conversation_id not in conversation_service._event_services

            # Verify directories were still removed
            assert mock_rmtree.call_count == 1

    @pytest.mark.asyncio
    async def test_delete_conversation_directory_removal_failure(
        self, conversation_service
    ):
        """Test delete_conversation succeeds even when directory removal fails."""
        conversation_id = uuid4()

        # Create mock event service
        mock_service = AsyncMock(spec=EventService)
        mock_service.conversation_dir = "/tmp/test_conversation"
        mock_service.stored = StoredConversation(
            id=conversation_id,
            agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="/tmp/test_workspace"),
            confirmation_policy=NeverConfirm(),
            initial_message=None,
            metrics=None,
            created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
        )
        mock_state = ConversationState(
            id=conversation_id,
            agent=mock_service.stored.agent,
            workspace=mock_service.stored.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=mock_service.stored.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        # Add to service
        conversation_service._event_services[conversation_id] = mock_service

        # Mock directory removal to fail (simulating permission errors)
        with patch(
            "openhands.agent_server.conversation_service.safe_rmtree"
        ) as mock_rmtree:
            mock_rmtree.return_value = False  # Simulate removal failure

            result = await conversation_service.delete_conversation(conversation_id)

            # Should still succeed - conversation is removed from tracking
            assert result is True
            assert conversation_id not in conversation_service._event_services

            # Verify event service was closed
            mock_service.close.assert_called_once()

            # Verify removal was attempted
            assert mock_rmtree.call_count == 1


class TestSafeRmtree:
    """Test cases for the _safe_rmtree helper function."""

    def test_safe_rmtree_nonexistent_path(self):
        """Test _safe_rmtree with non-existent path."""
        result = _safe_rmtree("/nonexistent/path", "test directory")
        assert result is True

    def test_safe_rmtree_empty_path(self):
        """Test _safe_rmtree with empty path."""
        result = _safe_rmtree("", "test directory")
        assert result is True

        result = _safe_rmtree(None, "test directory")
        assert result is True

    def test_safe_rmtree_success(self):
        """Test successful directory removal."""
        with tempfile.TemporaryDirectory() as temp_dir:
            test_dir = Path(temp_dir) / "test_subdir"
            test_dir.mkdir()

            # Create a test file
            test_file = test_dir / "test.txt"
            test_file.write_text("test content")

            result = _safe_rmtree(str(test_dir), "test directory")
            assert result is True
            assert not test_dir.exists()

    def test_safe_rmtree_permission_error(self):
        """Test _safe_rmtree handles permission errors gracefully."""
        with patch("shutil.rmtree") as mock_rmtree:
            mock_rmtree.side_effect = PermissionError("Permission denied")

            with patch("os.path.exists", return_value=True):
                result = _safe_rmtree("/test/path", "test directory")
                assert result is False

    def test_safe_rmtree_os_error(self):
        """Test _safe_rmtree handles OS errors gracefully."""
        with patch("shutil.rmtree") as mock_rmtree:
            mock_rmtree.side_effect = OSError("OS error")

            with patch("os.path.exists", return_value=True):
                result = _safe_rmtree("/test/path", "test directory")
                assert result is False

    def test_safe_rmtree_unexpected_error(self):
        """Test _safe_rmtree handles unexpected errors gracefully."""
        with patch("shutil.rmtree") as mock_rmtree:
            mock_rmtree.side_effect = ValueError("Unexpected error")

            with patch("os.path.exists", return_value=True):
                result = _safe_rmtree("/test/path", "test directory")
                assert result is False

    def test_safe_rmtree_readonly_file_handling(self):
        """Test _safe_rmtree handles read-only files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            test_dir = Path(temp_dir) / "test_subdir"
            test_dir.mkdir()

            # Create a test file and make it read-only
            test_file = test_dir / "readonly.txt"
            test_file.write_text("readonly content")
            test_file.chmod(0o444)  # Read-only

            result = _safe_rmtree(str(test_dir), "test directory")
            assert result is True
            assert not test_dir.exists()


class TestPluginLoading:
    """Test cases for plugin loading in ConversationService."""

    @pytest.fixture
    def mock_plugin(self):
        """Create a mock Plugin for testing."""
        from openhands.sdk.context.skills import Skill
        from openhands.sdk.plugin import Plugin
        from openhands.sdk.plugin.types import PluginManifest

        return Plugin(
            manifest=PluginManifest(
                name="test-plugin",
                version="1.0.0",
                description="A test plugin",
            ),
            path="/tmp/test-plugin",
            skills=[
                Skill(name="plugin-skill-1", content="Plugin skill 1 content"),
                Skill(name="plugin-skill-2", content="Plugin skill 2 content"),
            ],
            hooks=None,
            mcp_config={"test-mcp": {"command": "test"}},
            agents=[],
            commands=[],
        )

    def test_merge_plugin_into_request_with_skills(
        self, conversation_service, mock_plugin
    ):
        """Test merging plugin skills into a request without existing context."""
        request = StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="/tmp/test"),
        )

        result = conversation_service._merge_plugin_into_request(request, mock_plugin)

        # Verify skills were added
        assert result.agent.agent_context is not None
        assert len(result.agent.agent_context.skills) == 2
        skill_names = [s.name for s in result.agent.agent_context.skills]
        assert "plugin-skill-1" in skill_names
        assert "plugin-skill-2" in skill_names

    def test_merge_plugin_into_request_with_existing_skills(
        self, conversation_service, mock_plugin
    ):
        """Test merging plugin skills with existing agent skills."""
        from openhands.sdk import AgentContext
        from openhands.sdk.context.skills import Skill

        existing_skills = [
            Skill(name="existing-skill", content="Existing skill content"),
            Skill(
                name="plugin-skill-1", content="Original content"
            ),  # Will be overridden
        ]

        request = StartConversationRequest(
            agent=Agent(
                llm=LLM(model="gpt-4", usage_id="test-llm"),
                tools=[],
                agent_context=AgentContext(skills=existing_skills),
            ),
            workspace=LocalWorkspace(working_dir="/tmp/test"),
        )

        result = conversation_service._merge_plugin_into_request(request, mock_plugin)

        # Verify skills were merged correctly
        assert result.agent.agent_context is not None
        # existing + 2 plugin (1 override) = 3 skills
        assert len(result.agent.agent_context.skills) == 3

        # Verify plugin skill overrode existing skill with same name
        skill_by_name = {s.name: s for s in result.agent.agent_context.skills}
        assert skill_by_name["plugin-skill-1"].content == "Plugin skill 1 content"
        assert "existing-skill" in skill_by_name
        assert "plugin-skill-2" in skill_by_name

    def test_merge_plugin_into_request_with_mcp_config(
        self, conversation_service, mock_plugin
    ):
        """Test merging plugin MCP config."""
        request = StartConversationRequest(
            agent=Agent(
                llm=LLM(model="gpt-4", usage_id="test-llm"),
                tools=[],
                mcp_config={"existing-mcp": {"command": "existing"}},
            ),
            workspace=LocalWorkspace(working_dir="/tmp/test"),
        )

        result = conversation_service._merge_plugin_into_request(request, mock_plugin)

        # Verify MCP config was merged
        assert result.agent.mcp_config is not None
        assert "existing-mcp" in result.agent.mcp_config
        assert "test-mcp" in result.agent.mcp_config

    def test_merge_plugin_into_request_empty_plugin(self, conversation_service):
        """Test merging a plugin with no skills or MCP config."""
        from openhands.sdk.plugin import Plugin
        from openhands.sdk.plugin.types import PluginManifest

        empty_plugin = Plugin(
            manifest=PluginManifest(
                name="empty-plugin",
                version="1.0.0",
                description="An empty plugin",
            ),
            path="/tmp/empty-plugin",
            skills=[],
            hooks=None,
            mcp_config=None,
            agents=[],
            commands=[],
        )

        request = StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="/tmp/test"),
        )

        result = conversation_service._merge_plugin_into_request(request, empty_plugin)

        # Request should be unchanged - agent_context stays None
        assert result.agent.agent_context is None
        # mcp_config may default to {} in Agent, so check it wasn't modified from plugin
        # The key point is no plugin content was added

    @patch("openhands.agent_server.conversation_service.Plugin")
    def test_load_and_merge_plugin_success(
        self, mock_plugin_class, conversation_service, mock_plugin
    ):
        """Test successful plugin loading."""
        mock_plugin_class.fetch.return_value = Path("/tmp/test-plugin")
        mock_plugin_class.load.return_value = mock_plugin

        request = StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="/tmp/test"),
            plugin_source="github:test/plugin",
            plugin_ref="v1.0.0",
            plugin_path="plugins/sub-plugin",
        )

        result = conversation_service._load_and_merge_plugin(request)

        # Verify Plugin.fetch was called with correct args
        mock_plugin_class.fetch.assert_called_once_with(
            source="github:test/plugin",
            ref="v1.0.0",
            subpath="plugins/sub-plugin",
        )

        # Verify Plugin.load was called
        mock_plugin_class.load.assert_called_once_with(Path("/tmp/test-plugin"))

        # Verify skills were merged
        assert result.agent.agent_context is not None
        assert len(result.agent.agent_context.skills) == 2

    @patch("openhands.agent_server.conversation_service.Plugin")
    def test_load_and_merge_plugin_success_without_path(
        self, mock_plugin_class, conversation_service, mock_plugin
    ):
        """Test successful plugin loading without plugin_path."""
        mock_plugin_class.fetch.return_value = Path("/tmp/test-plugin")
        mock_plugin_class.load.return_value = mock_plugin

        request = StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="/tmp/test"),
            plugin_source="github:test/plugin",
            plugin_ref="v1.0.0",
        )

        result = conversation_service._load_and_merge_plugin(request)

        # Verify Plugin.fetch was called with subpath=None
        mock_plugin_class.fetch.assert_called_once_with(
            source="github:test/plugin",
            ref="v1.0.0",
            subpath=None,
        )

        # Verify Plugin.load was called
        mock_plugin_class.load.assert_called_once_with(Path("/tmp/test-plugin"))

        # Verify skills were merged
        assert result.agent.agent_context is not None
        assert len(result.agent.agent_context.skills) == 2

    @patch("openhands.agent_server.conversation_service.Plugin")
    def test_load_and_merge_plugin_fetch_error(
        self, mock_plugin_class, conversation_service
    ):
        """Test plugin loading when fetch fails."""
        from openhands.sdk.plugin import PluginFetchError

        mock_plugin_class.fetch.side_effect = PluginFetchError("Repository not found")

        request = StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="/tmp/test"),
            plugin_source="github:nonexistent/plugin",
        )

        with pytest.raises(PluginFetchError, match="Repository not found"):
            conversation_service._load_and_merge_plugin(request)

    @patch("openhands.agent_server.conversation_service.Plugin")
    def test_load_and_merge_plugin_load_error(
        self, mock_plugin_class, conversation_service
    ):
        """Test plugin loading when load fails."""
        from openhands.sdk.plugin import PluginFetchError

        mock_plugin_class.fetch.return_value = Path("/tmp/test-plugin")
        mock_plugin_class.load.side_effect = ValueError("Invalid plugin manifest")

        request = StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="/tmp/test"),
            plugin_source="github:test/invalid-plugin",
        )

        with pytest.raises(PluginFetchError, match="Failed to load plugin"):
            conversation_service._load_and_merge_plugin(request)

    def test_load_and_merge_plugin_no_source(self, conversation_service):
        """Test that plugin loading is skipped when no source is provided."""
        request = StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="/tmp/test"),
            plugin_source=None,
        )

        result = conversation_service._load_and_merge_plugin(request)

        # Request should be unchanged
        assert result is request

    def test_load_and_merge_plugin_whitespace_source(self, conversation_service):
        """Test that plugin loading is skipped for whitespace-only source."""
        request = StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="/tmp/test"),
            plugin_source="   ",  # Whitespace-only
        )

        result = conversation_service._load_and_merge_plugin(request)

        # Request should be unchanged (whitespace-only treated same as empty)
        assert result is request

    def test_load_and_merge_plugin_path_traversal(self, conversation_service):
        """Test that plugin loading fails for path traversal attempts."""
        from openhands.sdk.plugin import PluginFetchError

        request = StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="/tmp/test"),
            plugin_source="github:test/plugin",
            plugin_path="../../../etc/passwd",  # Path traversal attempt
        )

        with pytest.raises(
            PluginFetchError, match="cannot contain parent directory references"
        ):
            conversation_service._load_and_merge_plugin(request)

    def test_load_and_merge_plugin_path_traversal_nested(self, conversation_service):
        """Test that plugin loading fails for nested path traversal attempts."""
        from openhands.sdk.plugin import PluginFetchError

        request = StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="/tmp/test"),
            plugin_source="github:test/plugin",
            plugin_path="plugins/../../../secret",  # Nested path traversal
        )

        with pytest.raises(
            PluginFetchError, match="cannot contain parent directory references"
        ):
            conversation_service._load_and_merge_plugin(request)

    @patch("openhands.agent_server.conversation_service.Plugin")
    def test_load_and_merge_plugin_error_includes_exception_type(
        self, mock_plugin_class, conversation_service
    ):
        """Test that wrapped exceptions include the exception type name."""
        from openhands.sdk.plugin import PluginFetchError

        mock_plugin_class.fetch.return_value = Path("/tmp/test-plugin")
        mock_plugin_class.load.side_effect = TypeError("unexpected type error")

        request = StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="/tmp/test"),
            plugin_source="github:test/plugin",
        )

        with pytest.raises(PluginFetchError) as exc_info:
            conversation_service._load_and_merge_plugin(request)

        # Verify error message includes exception type name
        assert "TypeError:" in str(exc_info.value)
        assert "unexpected type error" in str(exc_info.value)

    def test_merge_plugin_into_request_too_many_skills(self, conversation_service):
        """Test that merging a plugin with too many skills raises an error."""
        from openhands.sdk.context.skills import Skill
        from openhands.sdk.plugin import Plugin, PluginFetchError
        from openhands.sdk.plugin.types import PluginManifest

        # Create a plugin with more than MAX_PLUGIN_SKILLS skills
        too_many_skills = [
            Skill(name=f"skill-{i}", content=f"Skill {i} content")
            for i in range(conversation_service.MAX_PLUGIN_SKILLS + 1)
        ]

        plugin = Plugin(
            manifest=PluginManifest(
                name="too-many-skills-plugin",
                version="1.0.0",
                description="A plugin with too many skills",
            ),
            path="/tmp/too-many-skills-plugin",
            skills=too_many_skills,
            hooks=None,
            mcp_config=None,
            agents=[],
            commands=[],
        )

        request = StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="/tmp/test"),
        )

        with pytest.raises(PluginFetchError, match="too many skills"):
            conversation_service._merge_plugin_into_request(request, plugin)

    def test_merge_plugin_into_request_at_skill_limit(
        self, conversation_service, mock_plugin
    ):
        """Test that merging a plugin at exactly MAX_PLUGIN_SKILLS succeeds."""
        from openhands.sdk.context.skills import Skill
        from openhands.sdk.plugin import Plugin
        from openhands.sdk.plugin.types import PluginManifest

        # Create a plugin with exactly MAX_PLUGIN_SKILLS skills
        max_skills = [
            Skill(name=f"skill-{i}", content=f"Skill {i} content")
            for i in range(conversation_service.MAX_PLUGIN_SKILLS)
        ]

        plugin = Plugin(
            manifest=PluginManifest(
                name="max-skills-plugin",
                version="1.0.0",
                description="A plugin with max skills",
            ),
            path="/tmp/max-skills-plugin",
            skills=max_skills,
            hooks=None,
            mcp_config=None,
            agents=[],
            commands=[],
        )

        request = StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="/tmp/test"),
        )

        # Should not raise
        result = conversation_service._merge_plugin_into_request(request, plugin)
        assert (
            len(result.agent.agent_context.skills)
            == conversation_service.MAX_PLUGIN_SKILLS
        )

    def test_merge_plugin_with_hooks_logs_warning(self, conversation_service, caplog):
        """Test that plugins with hooks log a warning."""
        import logging

        from openhands.sdk.hooks import HookConfig
        from openhands.sdk.plugin import Plugin
        from openhands.sdk.plugin.types import PluginManifest

        plugin_with_hooks = Plugin(
            manifest=PluginManifest(
                name="hooks-plugin",
                version="1.0.0",
                description="A plugin with hooks",
            ),
            path="/tmp/hooks-plugin",
            skills=[],
            hooks=HookConfig(hooks={"on_message": []}),  # Has hooks configured
            mcp_config=None,
            agents=[],
            commands=[],
        )

        request = StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="/tmp/test"),
        )

        with caplog.at_level(logging.WARNING):
            result = conversation_service._merge_plugin_into_request(
                request, plugin_with_hooks
            )

        # Verify warning was logged about hooks not being implemented
        assert "hooks configured" in caplog.text
        assert "not yet implemented" in caplog.text
        # Request should be unchanged (no skills or mcp to merge)
        assert result is request

    def test_merge_plugin_with_only_hooks_returns_unchanged(
        self, conversation_service, caplog
    ):
        """Test plugin with only hooks returns unchanged request after warning."""
        import logging

        from openhands.sdk.hooks import HookConfig
        from openhands.sdk.plugin import Plugin
        from openhands.sdk.plugin.types import PluginManifest

        # Plugin has hooks but no skills and no mcp_config
        plugin_only_hooks = Plugin(
            manifest=PluginManifest(
                name="only-hooks-plugin",
                version="1.0.0",
                description="A plugin with only hooks",
            ),
            path="/tmp/only-hooks-plugin",
            skills=[],
            hooks=HookConfig(hooks={"pre_run": []}),
            mcp_config=None,
            agents=[],
            commands=[],
        )

        request = StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="/tmp/test"),
        )

        with caplog.at_level(logging.WARNING):
            result = conversation_service._merge_plugin_into_request(
                request, plugin_only_hooks
            )

        # Warning should be logged
        assert "hooks configured" in caplog.text
        # Request should be unchanged since no skills/mcp were added
        assert result is request
        assert result.agent.agent_context is None

    def test_merge_plugin_mcp_config_overrides_same_key(self, conversation_service):
        """Test that plugin MCP config overrides existing config with same key."""
        from openhands.sdk.plugin import Plugin
        from openhands.sdk.plugin.types import PluginManifest

        plugin = Plugin(
            manifest=PluginManifest(
                name="override-plugin",
                version="1.0.0",
                description="A plugin that overrides MCP config",
            ),
            path="/tmp/override-plugin",
            skills=[],
            hooks=None,
            mcp_config={
                "shared-server": {"command": "new-command", "args": ["--new"]},
                "new-server": {"command": "brand-new"},
            },
            agents=[],
            commands=[],
        )

        request = StartConversationRequest(
            agent=Agent(
                llm=LLM(model="gpt-4", usage_id="test-llm"),
                tools=[],
                mcp_config={
                    "shared-server": {"command": "old-command", "args": ["--old"]},
                    "existing-server": {"command": "existing"},
                },
            ),
            workspace=LocalWorkspace(working_dir="/tmp/test"),
        )

        result = conversation_service._merge_plugin_into_request(request, plugin)

        # Plugin config should override existing with same key
        assert result.agent.mcp_config["shared-server"]["command"] == "new-command"
        assert result.agent.mcp_config["shared-server"]["args"] == ["--new"]
        # Existing server should still be there
        assert result.agent.mcp_config["existing-server"]["command"] == "existing"
        # New server from plugin should be added
        assert result.agent.mcp_config["new-server"]["command"] == "brand-new"

    def test_merge_skills_with_empty_existing_skills_list(self, conversation_service):
        """Test _merge_skills when existing context has empty skills list."""
        from openhands.sdk import AgentContext
        from openhands.sdk.context.skills import Skill

        # existing_context with empty skills list (not None)
        existing_context = AgentContext(skills=[])
        plugin_skills = [
            Skill(name="new-skill-1", content="Skill 1 content"),
            Skill(name="new-skill-2", content="Skill 2 content"),
        ]

        result = conversation_service._merge_skills(existing_context, plugin_skills)

        assert len(result.skills) == 2
        skill_names = [s.name for s in result.skills]
        assert "new-skill-1" in skill_names
        assert "new-skill-2" in skill_names

    def test_merge_skills_preserves_existing_context_attributes(
        self, conversation_service
    ):
        """Test that _merge_skills preserves other attributes of existing context."""
        from openhands.sdk import AgentContext
        from openhands.sdk.context.skills import Skill

        # Create context with skills and other attributes
        existing_context = AgentContext(
            skills=[Skill(name="existing", content="existing content")],
        )
        plugin_skills = [Skill(name="plugin", content="plugin content")]

        result = conversation_service._merge_skills(existing_context, plugin_skills)

        # Should have both skills
        assert len(result.skills) == 2
        # Result should be a new AgentContext (model_copy was used)
        assert result is not existing_context


class TestStartConversationWithPlugin:
    """Test cases for start_conversation with plugin loading."""

    @pytest.fixture
    def mock_plugin(self):
        """Create a mock Plugin for testing."""
        from openhands.sdk.context.skills import Skill
        from openhands.sdk.plugin import Plugin
        from openhands.sdk.plugin.types import PluginManifest

        return Plugin(
            manifest=PluginManifest(
                name="test-plugin",
                version="1.0.0",
                description="A test plugin",
            ),
            path="/tmp/test-plugin",
            skills=[
                Skill(name="plugin-skill-1", content="Plugin skill 1 content"),
                Skill(name="plugin-skill-2", content="Plugin skill 2 content"),
            ],
            hooks=None,
            mcp_config={"test-mcp": {"command": "test"}},
            agents=[],
            commands=[],
        )

    @pytest.mark.asyncio
    @patch("openhands.agent_server.conversation_service.Plugin")
    async def test_start_conversation_with_plugin_source(
        self, mock_plugin_class, conversation_service, mock_plugin
    ):
        """Test that start_conversation properly loads and merges plugins."""
        mock_plugin_class.fetch.return_value = Path("/tmp/test-plugin")
        mock_plugin_class.load.return_value = mock_plugin

        with tempfile.TemporaryDirectory() as temp_dir:
            request = StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                plugin_source="github:test/plugin",
            )

            with patch(
                "openhands.agent_server.conversation_service.EventService"
            ) as mock_event_service_class:
                mock_event_service = AsyncMock(spec=EventService)
                mock_event_service_class.return_value = mock_event_service

                # Mock the state that would be returned
                mock_state = ConversationState(
                    id=uuid4(),
                    agent=request.agent,
                    workspace=request.workspace,
                    execution_status=ConversationExecutionStatus.IDLE,
                    confirmation_policy=request.confirmation_policy,
                )
                mock_event_service.get_state.return_value = mock_state
                mock_event_service.stored = StoredConversation(
                    id=mock_state.id,
                    **request.model_dump(),
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )

                # Start the conversation
                result, is_new = await conversation_service.start_conversation(request)

                # Verify plugin was loaded
                mock_plugin_class.fetch.assert_called_once_with(
                    source="github:test/plugin",
                    ref=None,
                    subpath=None,
                )
                mock_plugin_class.load.assert_called_once()

                # Verify the stored conversation has merged plugin content
                stored_conversation = mock_event_service_class.call_args.kwargs[
                    "stored"
                ]
                assert stored_conversation.agent.agent_context is not None
                assert len(stored_conversation.agent.agent_context.skills) == 2
                assert "test-mcp" in stored_conversation.agent.mcp_config

                # Verify conversation was started
                assert is_new is True

    @pytest.mark.asyncio
    @patch("openhands.agent_server.conversation_service.Plugin")
    async def test_start_conversation_plugin_error_propagates(
        self, mock_plugin_class, conversation_service
    ):
        """Test PluginFetchError propagates through start_conversation."""
        from openhands.sdk.plugin import PluginFetchError

        mock_plugin_class.fetch.side_effect = PluginFetchError("Repository not found")

        with tempfile.TemporaryDirectory() as temp_dir:
            request = StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                plugin_source="github:nonexistent/repo",
            )

            with pytest.raises(PluginFetchError, match="Repository not found"):
                await conversation_service.start_conversation(request)

    @pytest.mark.asyncio
    @patch("openhands.agent_server.conversation_service.Plugin")
    async def test_start_conversation_with_plugin_ref_and_path(
        self, mock_plugin_class, conversation_service, mock_plugin
    ):
        """Test start_conversation passes plugin_ref and plugin_path correctly."""
        mock_plugin_class.fetch.return_value = Path("/tmp/test-plugin")
        mock_plugin_class.load.return_value = mock_plugin

        with tempfile.TemporaryDirectory() as temp_dir:
            request = StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                plugin_source="github:test/plugin",
                plugin_ref="v2.0.0",
                plugin_path="plugins/my-plugin",
            )

            with patch(
                "openhands.agent_server.conversation_service.EventService"
            ) as mock_event_service_class:
                mock_event_service = AsyncMock(spec=EventService)
                mock_event_service_class.return_value = mock_event_service

                mock_state = ConversationState(
                    id=uuid4(),
                    agent=request.agent,
                    workspace=request.workspace,
                    execution_status=ConversationExecutionStatus.IDLE,
                    confirmation_policy=request.confirmation_policy,
                )
                mock_event_service.get_state.return_value = mock_state
                mock_event_service.stored = StoredConversation(
                    id=mock_state.id,
                    **request.model_dump(),
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )

                await conversation_service.start_conversation(request)

                # Verify plugin was fetched with correct ref and path
                mock_plugin_class.fetch.assert_called_once_with(
                    source="github:test/plugin",
                    ref="v2.0.0",
                    subpath="plugins/my-plugin",
                )

    @pytest.mark.asyncio
    async def test_start_conversation_without_plugin_source(self, conversation_service):
        """Test start_conversation works normally when no plugin_source is provided."""
        with tempfile.TemporaryDirectory() as temp_dir:
            request = StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                # No plugin_source
            )

            with patch(
                "openhands.agent_server.conversation_service.EventService"
            ) as mock_event_service_class:
                mock_event_service = AsyncMock(spec=EventService)
                mock_event_service_class.return_value = mock_event_service

                mock_state = ConversationState(
                    id=uuid4(),
                    agent=request.agent,
                    workspace=request.workspace,
                    execution_status=ConversationExecutionStatus.IDLE,
                    confirmation_policy=request.confirmation_policy,
                )
                mock_event_service.get_state.return_value = mock_state
                mock_event_service.stored = StoredConversation(
                    id=mock_state.id,
                    **request.model_dump(),
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )

                result, is_new = await conversation_service.start_conversation(request)

                # Verify conversation was started without plugin loading
                assert is_new is True
                # Agent should not have any skills (no plugin loaded)
                stored = mock_event_service_class.call_args.kwargs["stored"]
                assert stored.agent.agent_context is None

    @pytest.mark.asyncio
    @patch("openhands.agent_server.conversation_service.Plugin")
    async def test_start_conversation_with_plugin_and_existing_context(
        self, mock_plugin_class, conversation_service
    ):
        """Test start_conversation merges plugin with existing agent context."""
        from openhands.sdk import AgentContext
        from openhands.sdk.context.skills import Skill
        from openhands.sdk.plugin import Plugin
        from openhands.sdk.plugin.types import PluginManifest

        # Plugin with one skill
        plugin = Plugin(
            manifest=PluginManifest(
                name="test-plugin",
                version="1.0.0",
                description="A test plugin",
            ),
            path="/tmp/test-plugin",
            skills=[Skill(name="plugin-skill", content="Plugin skill content")],
            hooks=None,
            mcp_config=None,
            agents=[],
            commands=[],
        )

        mock_plugin_class.fetch.return_value = Path("/tmp/test-plugin")
        mock_plugin_class.load.return_value = plugin

        with tempfile.TemporaryDirectory() as temp_dir:
            # Request with existing skills
            request = StartConversationRequest(
                agent=Agent(
                    llm=LLM(model="gpt-4", usage_id="test-llm"),
                    tools=[],
                    agent_context=AgentContext(
                        skills=[
                            Skill(name="existing-skill", content="Existing content")
                        ]
                    ),
                ),
                workspace=LocalWorkspace(working_dir=temp_dir),
                plugin_source="github:test/plugin",
            )

            with patch(
                "openhands.agent_server.conversation_service.EventService"
            ) as mock_event_service_class:
                mock_event_service = AsyncMock(spec=EventService)
                mock_event_service_class.return_value = mock_event_service

                mock_state = ConversationState(
                    id=uuid4(),
                    agent=request.agent,
                    workspace=request.workspace,
                    execution_status=ConversationExecutionStatus.IDLE,
                    confirmation_policy=request.confirmation_policy,
                )
                mock_event_service.get_state.return_value = mock_state
                mock_event_service.stored = StoredConversation(
                    id=mock_state.id,
                    **request.model_dump(),
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )

                await conversation_service.start_conversation(request)

                # Verify skills were merged (1 existing + 1 plugin = 2)
                stored = mock_event_service_class.call_args.kwargs["stored"]
                assert len(stored.agent.agent_context.skills) == 2
                skill_names = [s.name for s in stored.agent.agent_context.skills]
                assert "existing-skill" in skill_names
                assert "plugin-skill" in skill_names
