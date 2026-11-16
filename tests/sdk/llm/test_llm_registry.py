from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.llm_registry import LLMRegistry, RegistryEvent


def _llm(usage_id: str) -> LLM:
    return LLM(model="gpt-4o", usage_id=usage_id)


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

        # Add an LLM to trigger notification
        llm = _llm("notify-service")
        self.registry.add(llm)

        # Should receive notification for the newly added LLM, with the
        # session the registry keeps for its usage ID
        self.assertEqual(len(events_received), 1)
        self.assertIs(events_received[0].llm, llm)
        self.assertIs(
            events_received[0].session, self.registry.sessions["notify-service"]
        )

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
        mock_event = MagicMock()

        # This should handle the exception and log a warning (lines 146-147)
        registry.notify(mock_event)

        # Should have logged the warning
        mock_logger.warning.assert_called_once()
        assert "Failed to emit event:" in str(mock_logger.warning.call_args)


def test_llm_registry_list_usage_ids():
    """Test LLM registry list_usage_ids method."""

    registry = LLMRegistry()

    # Add some LLMs using the new API
    registry.add(_llm("service1"))
    registry.add(_llm("service2"))

    # Test list_usage_ids
    usage_ids = registry.list_usage_ids()

    assert "service1" in usage_ids
    assert "service2" in usage_ids
    assert len(usage_ids) == 2


def test_llm_registry_remove_method():
    """remove() drops a registered usage id and is a no-op when absent."""

    registry = LLMRegistry()

    registry.add(_llm("service1"))

    assert "service1" in registry.list_usage_ids()

    registry.remove("service1")
    assert "service1" not in registry.list_usage_ids()

    # Removing an unknown id is a no-op, not an error.
    registry.remove("service1")
    registry.remove("never-registered")


def test_llm_registry_readd_after_remove_keeps_metrics():
    from pydantic import SecretStr

    registry = LLMRegistry()
    llm = LLM(model="gpt-4o", api_key=SecretStr("test-key"), usage_id="slot")
    registry.add(llm)
    llm.metrics.add_cost(3.0)

    registry.remove("slot")
    registry.add(llm)

    assert llm.metrics.accumulated_cost == 3.0


def test_llm_registry_add_method():
    """Test the new add() method for LLMRegistry."""
    registry = LLMRegistry()

    llm = _llm("test-service")
    service_id = llm.usage_id

    # Test adding an LLM
    registry.add(llm)

    # Verify the LLM was added
    assert service_id in registry.usage_to_llm
    assert registry.usage_to_llm[service_id] is llm

    # Test that adding the same usage_id raises ValueError
    with unittest.TestCase().assertRaises(ValueError) as context:
        registry.add(llm)

    assert "already exists in registry" in str(context.exception)


def test_llm_registry_get_method():
    """Test the new get() method for LLMRegistry."""
    registry = LLMRegistry()

    llm = _llm("test-service")
    service_id = llm.usage_id

    # Add the LLM first
    registry.add(llm)

    # Test getting the LLM
    retrieved_llm = registry.get(service_id)
    assert retrieved_llm is llm

    # Test getting non-existent service raises KeyError
    with unittest.TestCase().assertRaises(KeyError) as context:
        registry.get("non-existent-service")

    assert "not found in registry" in str(context.exception)


def test_llm_registry_add_get_workflow():
    """Test the complete add/get workflow."""
    registry = LLMRegistry()

    llm1 = _llm("service1")
    llm2 = _llm("service2")

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


def test_llm_registry_ensures_independent_metrics_for_copied_llms():
    """Test registry ensures independent metrics for LLMs created via model_copy.

    This is important for scenarios like creating a condenser LLM from an agent
    LLM, where each should track its own usage independently. Without this fix,
    the metrics would be shared between the original and copied LLM, causing
    metrics to be double-counted when both LLMs are used.

    See: https://github.com/OpenHands/software-agent-sdk/issues/418
    """
    from pydantic import SecretStr

    registry = LLMRegistry()

    # Create original LLM
    original = LLM(
        model="gpt-4o",
        api_key=SecretStr("test-key"),
        usage_id="original-llm",
    )

    # Copy with updated usage_id (simulates creating a condenser LLM). A copy
    # made for another usage ID starts its own session, so it never shares the
    # original's metrics, registered or not.
    copied = original.model_copy(update={"usage_id": "copied-llm"})
    assert original.metrics is not copied.metrics

    registry.add(original)
    registry.add(copied)

    # They still have different metrics objects after registering
    assert original.metrics is not copied.metrics
    assert registry.sessions["original-llm"] is not registry.sessions["copied-llm"]

    # Verify metrics are independent - changes to one don't affect the other
    original.metrics.add_cost(1.0)
    assert original.metrics.accumulated_cost == 1.0
    assert copied.metrics.accumulated_cost == 0.0

    copied.metrics.add_cost(2.0)
    assert original.metrics.accumulated_cost == 1.0
    assert copied.metrics.accumulated_cost == 2.0


def test_llm_registry_ensures_independent_telemetry_for_copied_llms():
    """Test registry ensures independent telemetry for LLMs via model_copy.

    The telemetry object references the metrics object, so it must also be
    recreated to use the new metrics instance.
    """
    from pydantic import SecretStr

    registry = LLMRegistry()

    # Create original LLM
    original = LLM(
        model="gpt-4o",
        api_key=SecretStr("test-key"),
        usage_id="original-llm",
    )

    # Copy with updated usage_id: its own session means its own telemetry
    copied = original.model_copy(update={"usage_id": "copied-llm"})
    assert original.telemetry is not copied.telemetry
    assert copied.telemetry.metrics is copied.metrics

    # Register both LLMs
    registry.add(original)
    registry.add(copied)

    # They still have different telemetry objects after registering
    assert original.telemetry is not copied.telemetry


def test_llm_registry_separates_llms_that_share_a_session():
    """Two usage IDs never get one session, however the LLMs came to share it.

    A plain copy keeps the session of its source. Renaming it afterwards is
    the one way left to bring a shared session to the registry under two IDs.
    """
    registry = LLMRegistry()
    original = _llm("original-llm")
    renamed = original.model_copy()
    renamed.usage_id = "renamed-llm"
    assert renamed.metrics is original.metrics

    registry.add(original)
    registry.add(renamed)

    assert registry.sessions["original-llm"] is not registry.sessions["renamed-llm"]
    original.metrics.add_cost(1.0)
    assert renamed.metrics.accumulated_cost == 0.0


def test_llm_registry_does_not_reset_metrics_for_independent_llms():
    """Test registry does not reset metrics for LLMs with independent metrics."""
    from pydantic import SecretStr

    registry = LLMRegistry()

    # Create two independent LLMs (not via model_copy)
    llm1 = LLM(
        model="gpt-4o",
        api_key=SecretStr("test-key"),
        usage_id="llm1",
    )
    llm2 = LLM(
        model="gpt-4o",
        api_key=SecretStr("test-key"),
        usage_id="llm2",
    )

    # Add some cost to llm1's metrics before registering
    llm1.metrics.add_cost(5.0)
    original_metrics = llm1.metrics

    # Register both LLMs
    registry.add(llm1)
    registry.add(llm2)

    # llm1's metrics should not have been reset (it wasn't shared)
    assert llm1.metrics is original_metrics
    assert llm1.metrics.accumulated_cost == 5.0

    # llm2 should have its own independent metrics
    assert llm2.metrics is not llm1.metrics
    assert llm2.metrics.accumulated_cost == 0.0
