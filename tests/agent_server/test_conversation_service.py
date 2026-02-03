import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.agent_server.conversation_service import (
    ConversationService,
)
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
                    **request.model_dump(mode="json", context={"expose_secrets": True}),
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
                    **request.model_dump(mode="json", context={"expose_secrets": True}),
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
