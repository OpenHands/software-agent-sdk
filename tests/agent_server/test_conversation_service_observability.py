"""Tests for observability span management in ConversationService."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from openhands.agent_server.conversation_service import ConversationService


class TestConversationServiceObservability:
    """Tests for observability span lifecycle management."""

    @pytest.fixture
    def mock_laminar(self):
        """Create a mock Laminar class."""
        with patch(
            "openhands.agent_server.conversation_service.Laminar"
        ) as mock_laminar:
            mock_span = MagicMock()
            mock_span.is_recording.return_value = True
            mock_laminar.start_active_span.return_value = mock_span
            yield mock_laminar, mock_span

    @pytest.fixture
    def mock_observability_enabled(self):
        """Mock observability as enabled."""
        with patch(
            "openhands.agent_server.conversation_service.should_enable_observability",
            return_value=True,
        ):
            yield

    @pytest.fixture
    def mock_observability_disabled(self):
        """Mock observability as disabled."""
        with patch(
            "openhands.agent_server.conversation_service.should_enable_observability",
            return_value=False,
        ):
            yield

    @pytest.fixture
    def conversation_service(self, tmp_path):
        """Create a ConversationService instance."""
        service = ConversationService(conversations_dir=tmp_path)
        service._event_services = {}
        return service

    def test_start_conversation_span_when_enabled(
        self, conversation_service, mock_observability_enabled, mock_laminar
    ):
        """Test that span is started when observability is enabled."""
        mock_laminar_cls, mock_span = mock_laminar
        conversation_id = uuid4()

        conversation_service._start_conversation_span(conversation_id)

        mock_laminar_cls.start_active_span.assert_called_once_with("conversation")
        mock_laminar_cls.set_trace_session_id.assert_called_once_with(
            str(conversation_id)
        )
        assert conversation_id in conversation_service._conversation_spans
        assert conversation_service._conversation_spans[conversation_id] == mock_span

    def test_start_conversation_span_when_disabled(
        self, conversation_service, mock_observability_disabled, mock_laminar
    ):
        """Test that span is not started when observability is disabled."""
        mock_laminar_cls, _ = mock_laminar
        conversation_id = uuid4()

        conversation_service._start_conversation_span(conversation_id)

        mock_laminar_cls.start_active_span.assert_not_called()
        assert conversation_id not in conversation_service._conversation_spans

    def test_end_conversation_span(
        self, conversation_service, mock_observability_enabled, mock_laminar
    ):
        """Test that span is properly ended."""
        _, mock_span = mock_laminar
        conversation_id = uuid4()
        conversation_service._conversation_spans[conversation_id] = mock_span

        conversation_service._end_conversation_span(conversation_id)

        mock_span.is_recording.assert_called_once()
        mock_span.end.assert_called_once()
        assert conversation_id not in conversation_service._conversation_spans

    def test_end_conversation_span_not_recording(
        self, conversation_service, mock_observability_enabled, mock_laminar
    ):
        """Test that span.end() is not called if span is not recording."""
        _, mock_span = mock_laminar
        mock_span.is_recording.return_value = False
        conversation_id = uuid4()
        conversation_service._conversation_spans[conversation_id] = mock_span

        conversation_service._end_conversation_span(conversation_id)

        mock_span.is_recording.assert_called_once()
        mock_span.end.assert_not_called()
        assert conversation_id not in conversation_service._conversation_spans

    def test_end_conversation_span_nonexistent(self, conversation_service):
        """Test ending a span for a conversation that doesn't exist."""
        conversation_id = uuid4()

        # Should not raise an error
        conversation_service._end_conversation_span(conversation_id)

        assert conversation_id not in conversation_service._conversation_spans

    def test_end_all_conversation_spans(
        self, conversation_service, mock_observability_enabled, mock_laminar
    ):
        """Test that all spans are ended on cleanup."""
        _, mock_span = mock_laminar
        conv_id_1 = uuid4()
        conv_id_2 = uuid4()
        conv_id_3 = uuid4()

        # Create mock spans for each conversation
        mock_span_1 = MagicMock()
        mock_span_1.is_recording.return_value = True
        mock_span_2 = MagicMock()
        mock_span_2.is_recording.return_value = True
        mock_span_3 = MagicMock()
        mock_span_3.is_recording.return_value = True

        conversation_service._conversation_spans = {
            conv_id_1: mock_span_1,
            conv_id_2: mock_span_2,
            conv_id_3: mock_span_3,
        }

        conversation_service._end_all_conversation_spans()

        mock_span_1.end.assert_called_once()
        mock_span_2.end.assert_called_once()
        mock_span_3.end.assert_called_once()
        assert len(conversation_service._conversation_spans) == 0

    def test_span_lifecycle_start_pause_resume_delete(
        self, conversation_service, mock_observability_enabled, mock_laminar
    ):
        """Test full span lifecycle: start -> pause -> resume -> delete."""
        mock_laminar_cls, _ = mock_laminar
        conversation_id = uuid4()

        # Create unique mock spans for each start
        mock_span_1 = MagicMock()
        mock_span_1.is_recording.return_value = True
        mock_span_2 = MagicMock()
        mock_span_2.is_recording.return_value = True

        mock_laminar_cls.start_active_span.side_effect = [mock_span_1, mock_span_2]

        # 1. Start conversation - span should be created
        conversation_service._start_conversation_span(conversation_id)
        assert conversation_id in conversation_service._conversation_spans
        assert conversation_service._conversation_spans[conversation_id] == mock_span_1

        # 2. Pause - span should be ended
        conversation_service._end_conversation_span(conversation_id)
        mock_span_1.end.assert_called_once()
        assert conversation_id not in conversation_service._conversation_spans

        # 3. Resume - new span should be created
        conversation_service._start_conversation_span(conversation_id)
        assert conversation_id in conversation_service._conversation_spans
        assert conversation_service._conversation_spans[conversation_id] == mock_span_2

        # 4. Delete - span should be ended
        conversation_service._end_conversation_span(conversation_id)
        mock_span_2.end.assert_called_once()
        assert conversation_id not in conversation_service._conversation_spans

    def test_concurrent_conversations_isolated_spans(
        self, conversation_service, mock_observability_enabled, mock_laminar
    ):
        """Test that concurrent conversations have isolated spans."""
        mock_laminar_cls, _ = mock_laminar

        conv_id_a = uuid4()
        conv_id_b = uuid4()

        mock_span_a = MagicMock()
        mock_span_a.is_recording.return_value = True
        mock_span_b = MagicMock()
        mock_span_b.is_recording.return_value = True

        mock_laminar_cls.start_active_span.side_effect = [mock_span_a, mock_span_b]

        # Start both conversations
        conversation_service._start_conversation_span(conv_id_a)
        conversation_service._start_conversation_span(conv_id_b)

        assert len(conversation_service._conversation_spans) == 2
        assert conversation_service._conversation_spans[conv_id_a] == mock_span_a
        assert conversation_service._conversation_spans[conv_id_b] == mock_span_b

        # End conversation A - should not affect B
        conversation_service._end_conversation_span(conv_id_a)

        mock_span_a.end.assert_called_once()
        mock_span_b.end.assert_not_called()
        assert conv_id_a not in conversation_service._conversation_spans
        assert conv_id_b in conversation_service._conversation_spans

        # End conversation B
        conversation_service._end_conversation_span(conv_id_b)

        mock_span_b.end.assert_called_once()
        assert len(conversation_service._conversation_spans) == 0

    def test_span_end_handles_exception(
        self, conversation_service, mock_observability_enabled
    ):
        """Test that span end handles exceptions gracefully."""
        conversation_id = uuid4()
        mock_span = MagicMock()
        mock_span.is_recording.return_value = True
        mock_span.end.side_effect = Exception("Span end failed")

        conversation_service._conversation_spans[conversation_id] = mock_span

        # Should not raise, just log debug
        conversation_service._end_conversation_span(conversation_id)

        # Span should still be removed from tracking
        assert conversation_id not in conversation_service._conversation_spans
