import asyncio
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from litellm.types.utils import ChatCompletionMessageToolCall, Function
from pydantic import SecretStr

from openhands.agent_server.conversation_service import (
    AutoTitleSubscriber,
    ConversationContractMismatchError,
    ConversationService,
)
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import (
    ACPConversationInfo,
    ConversationInfo,
    ConversationPage,
    ConversationSortOrder,
    StartConversationRequest,
    StoredConversation,
    UpdateConversationRequest,
)
from openhands.agent_server.utils import safe_rmtree as _safe_rmtree
from openhands.sdk import LLM, Agent, Message
from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event import ActionEvent, AgentErrorEvent, ObservationEvent
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.secret import SecretSource, StaticSecret
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.security.risk import SecurityRisk
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.terminal.definition import TerminalAction, TerminalObservation


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
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir="workspace/project"),
        confirmation_policy=NeverConfirm(),
        initial_message=None,
        metrics=None,
        created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
        updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
    )


def _create_running_terminal_action(tool_call_id: str = "call_1") -> ActionEvent:
    tool_call = MessageToolCall.from_chat_tool_call(
        ChatCompletionMessageToolCall(
            id=tool_call_id,
            type="function",
            function=Function(
                name="terminal",
                arguments='{"command": "sleep 30"}',
            ),
        )
    )
    return ActionEvent(
        thought=[TextContent(text="run sleep")],
        action=TerminalAction(command="sleep 30"),
        tool_name="terminal",
        tool_call_id=tool_call_id,
        tool_call=tool_call,
        llm_response_id="response_1",
        security_risk=SecurityRisk.LOW,
        summary="run sleep",
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


@pytest.mark.asyncio
async def test_second_service_does_not_resume_active_running_conversation(tmp_path):
    """A second service should not attach to a live running conversation."""
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
    )

    async with ConversationService(conversations_dir=conversations_dir) as primary:
        conversation_info, _ = await primary.start_conversation(request)
        primary_event_service = primary._event_services[conversation_info.id]
        primary_state = await primary_event_service.get_state()

        running_action = _create_running_terminal_action()
        primary_state.events.append(running_action)
        primary_state.execution_status = ConversationExecutionStatus.RUNNING

        async with ConversationService(
            conversations_dir=conversations_dir,
        ) as secondary:
            assert conversation_info.id not in secondary._event_services

            primary_state.events.append(
                ObservationEvent(
                    observation=TerminalObservation.from_text(
                        "done",
                        command="sleep 30",
                        exit_code=0,
                    ),
                    action_id=running_action.id,
                    tool_name="terminal",
                    tool_call_id=running_action.tool_call_id,
                )
            )

        events = primary_state.events[:]
        assert [type(event).__name__ for event in events] == [
            "ActionEvent",
            "ConversationStateUpdateEvent",
            "ObservationEvent",
        ]
        assert not any(isinstance(event, AgentErrorEvent) for event in events)



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
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
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
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
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
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
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
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
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
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
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

    @pytest.mark.asyncio
    async def test_count_acp_conversations_includes_legacy_and_acp(
        self, conversation_service
    ):
        legacy_conversation = StoredConversation(
            id=uuid4(),
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="workspace/project"),
            confirmation_policy=NeverConfirm(),
            initial_message=None,
            metrics=None,
            created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
        )
        acp_conversation = StoredConversation(
            id=uuid4(),
            agent=ACPAgent(acp_command=["echo", "test"]),
            workspace=LocalWorkspace(working_dir="workspace/project"),
            confirmation_policy=NeverConfirm(),
            initial_message=None,
            metrics=None,
            created_at=datetime(2025, 1, 1, 13, 0, 0, tzinfo=UTC),
            updated_at=datetime(2025, 1, 1, 13, 30, 0, tzinfo=UTC),
        )

        for stored_conv in (legacy_conversation, acp_conversation):
            mock_service = AsyncMock(spec=EventService)
            mock_service.stored = stored_conv
            mock_service.get_state.return_value = ConversationState(
                id=stored_conv.id,
                agent=stored_conv.agent,
                workspace=stored_conv.workspace,
                execution_status=ConversationExecutionStatus.IDLE,
                confirmation_policy=stored_conv.confirmation_policy,
            )
            conversation_service._event_services[stored_conv.id] = mock_service

        assert await conversation_service.count_conversations() == 1
        assert await conversation_service.count_acp_conversations() == 2


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
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
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
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
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
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
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
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                confirmation_policy=NeverConfirm(),
                conversation_id=custom_id,
            )

            result, is_new = await conversation_service.start_conversation(request)
            assert result.id == custom_id
            assert is_new

            duplicate_request = StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
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
        mock_event_service.stored = StoredConversation(
            id=custom_id,
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir="workspace/project"),
            confirmation_policy=NeverConfirm(),
            initial_message=None,
            metrics=None,
            created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
        )
        conversation_service._event_services[custom_id] = mock_event_service

        with tempfile.TemporaryDirectory() as temp_dir:
            request = StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
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
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
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
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
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
    async def test_start_conversation_rejects_existing_acp_conversation_id(
        self, conversation_service
    ):
        custom_id = uuid4()

        mock_event_service = AsyncMock(spec=EventService)
        mock_event_service.is_open.return_value = True
        mock_event_service.stored = StoredConversation(
            id=custom_id,
            agent=ACPAgent(acp_command=["echo", "test"]),
            workspace=LocalWorkspace(working_dir="workspace/project"),
            confirmation_policy=NeverConfirm(),
            initial_message=None,
            metrics=None,
            created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
        )
        conversation_service._event_services[custom_id] = mock_event_service

        with tempfile.TemporaryDirectory() as temp_dir:
            request = StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
                workspace=LocalWorkspace(working_dir=temp_dir),
                confirmation_policy=NeverConfirm(),
                conversation_id=custom_id,
            )

            with patch.object(
                conversation_service, "_start_event_service"
            ) as mock_start:
                with pytest.raises(
                    ConversationContractMismatchError,
                    match="only available through the ACP conversation contract",
                ):
                    await conversation_service.start_conversation(request)

                mock_start.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_event_service_failure_cleanup(self, conversation_service):
        """Test that event service is cleaned up when startup fails."""
        with tempfile.TemporaryDirectory() as temp_dir:
            stored = StoredConversation(
                id=uuid4(),
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
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
                agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
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
    async def test_update_conversation_tags_uses_state_lock(
        self, conversation_service, sample_stored_conversation
    ):
        """Test that tag updates hold the ConversationState lock."""
        mock_service = AsyncMock(spec=EventService)
        mock_service.stored = sample_stored_conversation
        mock_state = ConversationState(
            id=sample_stored_conversation.id,
            agent=sample_stored_conversation.agent,
            workspace=sample_stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=sample_stored_conversation.confirmation_policy,
        )
        acquire_spy = MagicMock(wraps=mock_state._lock.acquire)
        release_spy = MagicMock(wraps=mock_state._lock.release)
        mock_state._lock.acquire = acquire_spy
        mock_state._lock.release = release_spy
        mock_service.get_state.return_value = mock_state

        conversation_id = sample_stored_conversation.id
        conversation_service._event_services[conversation_id] = mock_service

        request = UpdateConversationRequest(tags={"env": "prod"})
        result = await conversation_service.update_conversation(
            conversation_id, request
        )

        assert result is True
        assert mock_service.stored.tags == {"env": "prod"}
        assert mock_state.tags == {"env": "prod"}
        assert acquire_spy.call_count >= 2
        assert release_spy.call_count == acquire_spy.call_count

    @pytest.mark.asyncio
    async def test_update_conversation_tags_wait_does_not_block_event_loop(
        self, conversation_service, sample_stored_conversation
    ):
        """Waiting on the state lock must not stall unrelated async work."""
        mock_service = AsyncMock(spec=EventService)
        mock_service.stored = sample_stored_conversation
        state = ConversationState(
            id=sample_stored_conversation.id,
            agent=sample_stored_conversation.agent,
            workspace=sample_stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=sample_stored_conversation.confirmation_policy,
        )
        mock_service.get_state.return_value = state

        conversation_id = sample_stored_conversation.id
        conversation_service._event_services[conversation_id] = mock_service

        lock_acquired = threading.Event()
        release_lock = threading.Event()
        timings: dict[str, float] = {}

        def hold_state_lock() -> None:
            with state:
                timings["lock_start"] = time.monotonic()
                lock_acquired.set()
                release_lock.wait(timeout=1.0)
                timings["lock_end"] = time.monotonic()

        holder = threading.Thread(target=hold_state_lock, daemon=True)
        holder.start()
        assert lock_acquired.wait(timeout=1.0)

        async def heartbeat() -> None:
            await asyncio.sleep(0.05)
            timings["heartbeat"] = time.monotonic()

        async def release_after_delay() -> None:
            await asyncio.sleep(0.2)
            release_lock.set()

        with patch.object(
            conversation_service, "_notify_conversation_webhooks", new=AsyncMock()
        ):
            await asyncio.wait_for(
                asyncio.gather(
                    conversation_service.update_conversation(
                        conversation_id,
                        UpdateConversationRequest(tags={"env": "prod"}),
                    ),
                    heartbeat(),
                    release_after_delay(),
                ),
                timeout=1.0,
            )

        holder.join(timeout=1.0)
        assert not holder.is_alive()
        assert mock_service.stored.tags == {"env": "prod"}
        assert state.tags == {"env": "prod"}
        assert timings["heartbeat"] < timings["lock_end"], (
            "update_conversation blocked the async loop while waiting for the "
            "state lock"
        )

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
            assert isinstance(conversation_info, ConversationInfo)

    @pytest.mark.asyncio
    async def test_update_acp_conversation_notifies_webhooks_with_acp_shape(
        self, conversation_service
    ):
        stored_conversation = StoredConversation(
            id=uuid4(),
            agent=ACPAgent(acp_command=["echo", "test"]),
            workspace=LocalWorkspace(working_dir="workspace/project"),
            confirmation_policy=NeverConfirm(),
            initial_message=None,
            metrics=None,
            created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2025, 1, 1, 12, 30, 0, tzinfo=UTC),
        )
        mock_service = AsyncMock(spec=EventService)
        mock_service.stored = stored_conversation
        mock_state = ConversationState(
            id=stored_conversation.id,
            agent=stored_conversation.agent,
            workspace=stored_conversation.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=stored_conversation.confirmation_policy,
        )
        mock_service.get_state.return_value = mock_state

        conversation_id = stored_conversation.id
        conversation_service._event_services[conversation_id] = mock_service

        with patch.object(
            conversation_service, "_notify_conversation_webhooks", new=AsyncMock()
        ) as mock_notify:
            result = await conversation_service.update_conversation(
                conversation_id, UpdateConversationRequest(title="ACP Title")
            )

            assert result is True
            mock_notify.assert_called_once()
            conversation_info = mock_notify.call_args[0][0]
            assert isinstance(conversation_info, ACPConversationInfo)
            assert conversation_info.agent.kind == "ACPAgent"

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

    @pytest.mark.asyncio
    async def test_update_conversation_sets_updated_at(
        self, conversation_service, sample_stored_conversation
    ):
        """Test that update_conversation advances updated_at.

        Renaming a conversation is a meaningful change; the timestamp must
        reflect when it happened rather than staying at the value set at
        conversation creation time.
        """
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

        original_updated_at = mock_service.stored.updated_at

        request = UpdateConversationRequest(title="New Title")
        await conversation_service.update_conversation(conversation_id, request)

        assert mock_service.stored.updated_at > original_updated_at


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
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
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
                assert isinstance(conversation_info, ConversationInfo)

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
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
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
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
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
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
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


class TestAutoTitle:
    """Tests for AutoTitleSubscriber."""

    _GENERATE_TITLE_PATH = (
        "openhands.agent_server.conversation_service.generate_title_from_message"
    )

    def _make_service(
        self,
        title: str | None = None,
        title_llm_profile: str | None = None,
        llm_model: str = "gpt-4o",
        llm_usage_id: str = "test-llm",
    ) -> AsyncMock:
        stored = StoredConversation(
            id=uuid4(),
            agent=Agent(llm=LLM(model=llm_model, usage_id=llm_usage_id), tools=[]),
            workspace=LocalWorkspace(working_dir="workspace/project"),
            confirmation_policy=NeverConfirm(),
            initial_message=None,
            metrics=None,
            title=title,
            title_llm_profile=title_llm_profile,
        )
        service = AsyncMock(spec=EventService)
        service.stored = stored

        mock_conversation = MagicMock()
        mock_conversation.agent.llm = stored.agent.llm
        service._conversation = mock_conversation
        return service

    def _user_message_event(self, text: str = "Fix the login bug") -> MessageEvent:
        from openhands.sdk.llm.message import TextContent

        return MessageEvent(
            id="evt-1",
            source="user",
            llm_message=Message(role="user", content=[TextContent(text=text)]),
        )

    @staticmethod
    async def _drain_title_task(
        predicate=lambda: True, max_iterations: int = 50, step: float = 0.02
    ) -> None:
        """Yield to the event loop until the background title task completes.

        `AutoTitleSubscriber` schedules generation via `run_in_executor`, so a
        single `await asyncio.sleep(0)` is not enough to let the executor
        thread finish. Poll with a short sleep until `predicate()` becomes
        truthy or the timeout elapses.
        """
        for _ in range(max_iterations):
            await asyncio.sleep(step)
            if predicate():
                return

    @pytest.mark.asyncio
    async def test_autotitle_sets_title_on_first_user_message(self):
        """Title is generated and saved when the first user message arrives."""
        service = self._make_service()

        with patch(self._GENERATE_TITLE_PATH, return_value="✨ Generated Title"):
            subscriber = AutoTitleSubscriber(service=service)
            await subscriber(self._user_message_event())
            await asyncio.sleep(0)

        assert service.stored.title == "✨ Generated Title"
        service.save_meta.assert_called_once()

    @pytest.mark.asyncio
    async def test_autotitle_skips_non_user_events(self):
        """Non-user events do not trigger title generation.

        Covers ConversationStateUpdateEvent and assistant MessageEvents.
        """
        service = self._make_service()
        subscriber = AutoTitleSubscriber(service=service)

        # ConversationStateUpdateEvent should be ignored
        await subscriber(
            ConversationStateUpdateEvent(key="execution_status", value="IDLE")
        )
        # Assistant MessageEvent should be ignored
        await subscriber(
            MessageEvent(
                id="evt-2", source="agent", llm_message=Message(role="assistant")
            )
        )

        await asyncio.sleep(0)
        assert service.stored.title is None

    @pytest.mark.asyncio
    async def test_autotitle_skips_when_title_already_set(self):
        """No LLM call is made when the conversation already has a title."""
        service = self._make_service(title="Existing Title")
        subscriber = AutoTitleSubscriber(service=service)

        with patch(self._GENERATE_TITLE_PATH) as mock_generate_title:
            await subscriber(self._user_message_event())
            await asyncio.sleep(0)
            mock_generate_title.assert_not_called()

        assert service.stored.title == "Existing Title"

    @pytest.mark.asyncio
    async def test_autotitle_handles_generate_title_failure(self):
        """A failed title generation is logged as a warning and not re-raised."""
        service = self._make_service()

        with patch(self._GENERATE_TITLE_PATH, side_effect=Exception("LLM unavailable")):
            subscriber = AutoTitleSubscriber(service=service)
            # Should not raise
            await subscriber(self._user_message_event())
            await asyncio.sleep(0)

        # Title remains unset; save_meta was never called
        assert service.stored.title is None
        service.save_meta.assert_not_called()

    @pytest.mark.asyncio
    async def test_autotitle_skips_empty_message(self):
        """No title generation if the user message has no text content."""
        service = self._make_service()
        event = MessageEvent(
            id="evt-1", source="user", llm_message=Message(role="user")
        )

        with patch(self._GENERATE_TITLE_PATH) as mock_generate_title:
            subscriber = AutoTitleSubscriber(service=service)
            await subscriber(event)
            await asyncio.sleep(0)
            mock_generate_title.assert_not_called()

        assert service.stored.title is None

    @pytest.mark.asyncio
    async def test_autotitle_uses_llm_profile_when_configured(self):
        """Profile LLM takes precedence over agent.llm when configured."""
        service = self._make_service(title_llm_profile="cheap-model")
        mock_llm = LLM(model="gpt-3.5-turbo", usage_id="title-llm")

        with (
            patch("openhands.sdk.llm.llm_profile_store.LLMProfileStore") as MockStore,
            patch(
                self._GENERATE_TITLE_PATH, return_value="✨ Profile LLM Title"
            ) as mock_generate_title,
        ):
            mock_store_instance = MockStore.return_value
            mock_store_instance.load.return_value = mock_llm

            subscriber = AutoTitleSubscriber(service=service)
            await subscriber(self._user_message_event())
            await self._drain_title_task(lambda: service.stored.title is not None)

            MockStore.assert_called_once_with()
            mock_store_instance.load.assert_called_once_with("cheap-model")
            # Profile-loaded LLM wins over agent.llm
            assert mock_generate_title.called
            assert mock_generate_title.call_args.args[1] is mock_llm

        assert service.stored.title == "✨ Profile LLM Title"
        service.save_meta.assert_called_once()

    @pytest.mark.asyncio
    async def test_autotitle_falls_back_to_agent_llm_when_profile_not_found(self):
        """Missing profile → fall back to agent.llm (non-breaking behavior)."""
        service = self._make_service(title_llm_profile="nonexistent-profile")
        agent_llm = service._conversation.agent.llm

        with (
            patch("openhands.sdk.llm.llm_profile_store.LLMProfileStore") as MockStore,
            patch(
                self._GENERATE_TITLE_PATH, return_value="✨ Agent LLM Title"
            ) as mock_generate_title,
        ):
            mock_store_instance = MockStore.return_value
            mock_store_instance.load.side_effect = FileNotFoundError(
                "Profile 'nonexistent-profile' not found"
            )

            subscriber = AutoTitleSubscriber(service=service)
            await subscriber(self._user_message_event())
            await self._drain_title_task(lambda: service.stored.title is not None)

            # Failed profile load → falls back to agent.llm
            assert mock_generate_title.called
            assert mock_generate_title.call_args.args[1] is agent_llm

        assert service.stored.title == "✨ Agent LLM Title"
        service.save_meta.assert_called_once()

    @pytest.mark.asyncio
    async def test_autotitle_no_profile_uses_agent_llm(self):
        """No profile configured → use agent.llm (preserves existing behavior)."""
        service = self._make_service(title_llm_profile=None)
        agent_llm = service._conversation.agent.llm

        with patch(
            self._GENERATE_TITLE_PATH, return_value="✨ Agent LLM Title"
        ) as mock_generate_title:
            subscriber = AutoTitleSubscriber(service=service)
            await subscriber(self._user_message_event())
            await self._drain_title_task(lambda: service.stored.title is not None)

            # No profile → agent.llm is used (backwards compatible)
            assert mock_generate_title.called
            assert mock_generate_title.call_args.args[1] is agent_llm

        assert service.stored.title == "✨ Agent LLM Title"
        service.save_meta.assert_called_once()

    @pytest.mark.asyncio
    async def test_autotitle_handles_profile_load_value_error(self):
        """Profile load ValueError → fall back to agent.llm."""
        service = self._make_service(title_llm_profile="corrupted-profile")
        agent_llm = service._conversation.agent.llm

        with (
            patch("openhands.sdk.llm.llm_profile_store.LLMProfileStore") as MockStore,
            patch(
                self._GENERATE_TITLE_PATH, return_value="✨ Agent LLM Title"
            ) as mock_generate_title,
        ):
            mock_store_instance = MockStore.return_value
            mock_store_instance.load.side_effect = ValueError("Invalid profile format")

            subscriber = AutoTitleSubscriber(service=service)
            await subscriber(self._user_message_event())
            await self._drain_title_task(lambda: service.stored.title is not None)

            assert mock_generate_title.called
            assert mock_generate_title.call_args.args[1] is agent_llm

        assert service.stored.title == "✨ Agent LLM Title"
        service.save_meta.assert_called_once()

    @pytest.mark.asyncio
    async def test_autotitle_falls_back_for_acp_managed_llm(self):
        """ACP-managed agents with no title profile → truncation fallback."""
        service = self._make_service(llm_usage_id="acp-managed")
        subscriber = AutoTitleSubscriber(service=service)

        await subscriber(self._user_message_event("Fix the login bug"))
        await self._drain_title_task(lambda: service.stored.title is not None)

        assert service.stored.title == "Fix the login bug"
        service.save_meta.assert_called_once()

    @pytest.mark.asyncio
    async def test_autotitle_integration_routes_through_profile_store(self, tmp_path):
        """End-to-end: profile on disk → LLMProfileStore.load → title LLM call.

        Exercises the real wiring from AutoTitleSubscriber through LLMProfileStore
        to LLM.completion. Only the network boundary (LLM.completion) is mocked,
        so this catches regressions in profile loading, LLM passthrough, and the
        agent-server → SDK integration — the unit tests above only exercise
        AutoTitleSubscriber in isolation.
        """
        from litellm.types.utils import (
            Choices,
            Message as LiteLLMMessage,
            ModelResponse,
            Usage,
        )

        from openhands.sdk.llm import LLMResponse, MetricsSnapshot
        from openhands.sdk.llm.llm_profile_store import LLMProfileStore

        # Persist a real LLM profile to disk with a distinctive usage_id so we
        # can tell the title LLM apart from the agent's LLM in the assertion.
        profile_dir = tmp_path / "profiles"
        title_llm_on_disk = LLM(
            usage_id="title-llm",
            model="claude-haiku-4-5",
            api_key=SecretStr("title-key"),
        )
        LLMProfileStore(base_dir=profile_dir).save(
            "title-fast", title_llm_on_disk, include_secrets=True
        )

        service = self._make_service(title_llm_profile="title-fast")

        calls: list[str] = []

        def fake_completion(self_llm, _messages, **_kwargs):
            calls.append(self_llm.usage_id)
            msg = LiteLLMMessage(content="✨ Generated", role="assistant")
            choice = Choices(finish_reason="stop", index=0, message=msg)
            raw = ModelResponse(
                id="resp-1",
                choices=[choice],
                created=0,
                model=self_llm.model,
                object="chat.completion",
                usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            )
            return LLMResponse(
                message=Message.from_llm_chat_message(choice["message"]),
                metrics=MetricsSnapshot(
                    model_name=self_llm.model,
                    accumulated_cost=0.0,
                    max_budget_per_task=None,
                    accumulated_token_usage=None,
                ),
                raw_response=raw,
            )

        # Point LLMProfileStore() (no args) at our tmp dir so the real
        # _load_title_llm code path finds our on-disk profile.
        with (
            patch(
                "openhands.sdk.llm.llm_profile_store._DEFAULT_PROFILE_DIR", profile_dir
            ),
            patch(
                "openhands.sdk.llm.llm.LLM.completion",
                autospec=True,
                side_effect=fake_completion,
            ),
        ):
            subscriber = AutoTitleSubscriber(service=service)
            await subscriber(self._user_message_event("Fix the login bug"))
            # Wait for the background executor task to complete. The production
            # code uses run_in_executor, so sleep(0) is not enough.
            for _ in range(50):
                await asyncio.sleep(0.02)
                if service.stored.title is not None:
                    break

        # The profile's LLM (usage_id="title-llm") was called — not agent.llm
        # (usage_id="test-llm"). This is the regression-sensitive assertion.
        assert calls == ["title-llm"], (
            f"Expected only the title profile LLM to be called, got: {calls}"
        )
        assert service.stored.title == "✨ Generated"
        service.save_meta.assert_called_once()


class TestACPActivityHeartbeatWiring:
    """Tests for _setup_acp_activity_heartbeat in EventService."""

    def test_acp_agent_gets_on_activity_wired(self):
        """_setup_acp_activity_heartbeat should set _on_activity on ACPAgent."""
        from openhands.agent_server.event_service import EventService
        from openhands.agent_server.server_details_router import (
            update_last_execution_time,
        )

        service = AsyncMock(spec=EventService)
        # Call the real method
        agent = ACPAgent(acp_command=["echo", "test"])
        assert agent._on_activity is None

        EventService._setup_acp_activity_heartbeat(service, agent)

        assert agent._on_activity is update_last_execution_time

    def test_non_acp_agent_unchanged(self):
        """_setup_acp_activity_heartbeat is a no-op for non-ACP agents."""
        from openhands.agent_server.event_service import EventService

        service = AsyncMock(spec=EventService)
        agent = Agent(llm=LLM(model="test-model"))

        # Should not raise and should not set any attribute
        EventService._setup_acp_activity_heartbeat(service, agent)
        assert not hasattr(agent, "_on_activity")
