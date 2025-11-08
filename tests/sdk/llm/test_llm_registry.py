from __future__ import annotations

import unittest
from unittest.mock import MagicMock, Mock, patch

from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.llm_registry import LLMRegistry, RegistryEvent


class TestLLMRegistry(unittest.TestCase):
    def setUp(self):
        """Set up test environment before each test."""
        # Create a registry for testing
        self.registry: LLMRegistry = LLMRegistry()

    def test_subscribe_and_notify(self):
        """Test the subscription and notification system."""
        events_received = []

        def callback(event: RegistryEvent):
            events_received.append(event)

        # Subscribe to events
        self.registry.subscribe(callback)

        # Create a mock LLM and add it to trigger notification
        mock_llm = Mock(spec=LLM)
        mock_llm.usage_id = "notify-service"

        # Mock the RegistryEvent to avoid LLM attribute access
        with patch(
            "openhands.sdk.llm.llm_registry.RegistryEvent"
        ) as mock_registry_event:
            mock_registry_event.return_value = Mock()
            self.registry.add(mock_llm)

        # Should receive notification for the newly added LLM
        self.assertEqual(len(events_received), 1)

        # Test that the subscriber is set correctly
        self.assertIsNotNone(self.registry.subscriber)

        # Test notify method directly with a mock event
        with patch.object(self.registry, "subscriber") as mock_subscriber:
            mock_event = MagicMock()
            self.registry.notify(mock_event)
            mock_subscriber.assert_called_once_with(mock_event)

    def test_registry_has_unique_id(self):
        """Test that each registry instance has a unique ID."""
        registry2 = LLMRegistry()
        self.assertNotEqual(self.registry.registry_id, registry2.registry_id)
        self.assertTrue(len(self.registry.registry_id) > 0)
        self.assertTrue(len(registry2.registry_id) > 0)


def test_llm_registry_notify_exception_handling():
    """Test LLM registry handles exceptions in subscriber notification."""

    # Create a subscriber that raises an exception
    def failing_subscriber(event):
        raise ValueError("Subscriber failed")

    registry = LLMRegistry()
    registry.subscribe(failing_subscriber)

    # Mock the logger to capture warning messages
    with patch("openhands.sdk.llm.llm_registry.logger") as mock_logger:
        # Create a mock event
        mock_event = Mock()

        # This should handle the exception and log a warning (lines 146-147)
        registry.notify(mock_event)

        # Should have logged the warning
        mock_logger.warning.assert_called_once()
        assert "Failed to emit event:" in str(mock_logger.warning.call_args)


def test_llm_registry_list_usage_ids():
    """Test LLM registry list_usage_ids method."""

    registry = LLMRegistry()

    # Create mock LLM objects
    mock_llm1 = Mock(spec=LLM)
    mock_llm1.usage_id = "service1"
    mock_llm2 = Mock(spec=LLM)
    mock_llm2.usage_id = "service2"

    # Mock the RegistryEvent to avoid LLM attribute access
    with patch("openhands.sdk.llm.llm_registry.RegistryEvent") as mock_registry_event:
        mock_registry_event.return_value = Mock()

        # Add some LLMs using the new API
        registry.add(mock_llm1)
        registry.add(mock_llm2)

        # Test list_usage_ids
        usage_ids = registry.list_usage_ids()

        assert "service1" in usage_ids
        assert "service2" in usage_ids
        assert len(usage_ids) == 2


def test_llm_registry_add_method():
    """Test the new add() method for LLMRegistry."""
    registry = LLMRegistry()

    # Create a mock LLM
    mock_llm = Mock(spec=LLM)
    mock_llm.usage_id = "test-service"
    service_id = mock_llm.usage_id

    # Mock the RegistryEvent to avoid LLM attribute access
    with patch("openhands.sdk.llm.llm_registry.RegistryEvent") as mock_registry_event:
        mock_registry_event.return_value = Mock()

        # Test adding an LLM
        registry.add(mock_llm)

        # Verify the LLM was added
        assert service_id in registry.usage_to_llm
        assert registry.usage_to_llm[service_id] is mock_llm

        # Verify RegistryEvent was called
        mock_registry_event.assert_called_once_with(llm=mock_llm)

    # Test that adding the same usage_id raises ValueError
    with unittest.TestCase().assertRaises(ValueError) as context:
        registry.add(mock_llm)

    assert "already exists in registry" in str(context.exception)


def test_llm_registry_get_method():
    """Test the new get() method for LLMRegistry."""
    registry = LLMRegistry()

    # Create a mock LLM
    mock_llm = Mock(spec=LLM)
    mock_llm.usage_id = "test-service"
    service_id = mock_llm.usage_id

    # Mock the RegistryEvent to avoid LLM attribute access
    with patch("openhands.sdk.llm.llm_registry.RegistryEvent") as mock_registry_event:
        mock_registry_event.return_value = Mock()

        # Add the LLM first
        registry.add(mock_llm)

        # Test getting the LLM
        retrieved_llm = registry.get(service_id)
        assert retrieved_llm is mock_llm

    # Test getting non-existent service raises KeyError
    with unittest.TestCase().assertRaises(KeyError) as context:
        registry.get("non-existent-service")

    assert "not found in registry" in str(context.exception)


def test_llm_registry_add_get_workflow():
    """Test the complete add/get workflow."""
    registry = LLMRegistry()

    # Create mock LLMs
    llm1 = Mock(spec=LLM)
    llm1.usage_id = "service1"
    llm2 = Mock(spec=LLM)
    llm2.usage_id = "service2"

    # Mock the RegistryEvent to avoid LLM attribute access
    with patch("openhands.sdk.llm.llm_registry.RegistryEvent") as mock_registry_event:
        mock_registry_event.return_value = Mock()

        # Add multiple LLMs
        registry.add(llm1)
        registry.add(llm2)

        # Verify we can retrieve them
        assert registry.get("service1") is llm1
        assert registry.get("service2") is llm2

        # Verify list_usage_ids works
        usage_ids = registry.list_usage_ids()
        assert "service1" in usage_ids
        assert "service2" in usage_ids
        assert len(usage_ids) == 2

        # Verify usage_id is set correctly
        assert llm1.usage_id == "service1"
        assert llm2.usage_id == "service2"
