import tempfile
import uuid
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, ConversationStats, LLMRegistry, RegistryEvent
from openhands.sdk.io.local import LocalFileStore
from openhands.sdk.llm.utils.metrics import Metrics


# Test UUIDs
TEST_CONVERSATION_ID = uuid.UUID("12345678-1234-5678-9abc-123456789abc")
CONV_MERGE_A_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
CONV_MERGE_B_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


@pytest.fixture
def mock_file_store():
    """Create a mock file store for testing."""
    return LocalFileStore(root=tempfile.mkdtemp())


@pytest.fixture
def conversation_stats(mock_file_store):
    """Create a ConversationStats instance for testing."""
    return ConversationStats()


@pytest.fixture
def mock_llm_registry():
    """Create a mock LLM registry that properly simulates LLM registration."""
    registry = LLMRegistry()
    return registry


@pytest.fixture
def connected_registry_and_stats(mock_llm_registry, conversation_stats):
    """Connect the LLMRegistry and ConversationStats properly."""
    # Subscribe to LLM registry events to track metrics
    mock_llm_registry.subscribe(conversation_stats.register_llm)
    return mock_llm_registry, conversation_stats


def test_get_combined_metrics(conversation_stats):
    """Test that combined metrics are calculated correctly."""
    # Add multiple services with metrics
    service1 = "service1"
    metrics1 = Metrics(model_name="gpt-4")
    metrics1.add_cost(0.05)
    metrics1.add_token_usage(
        prompt_tokens=100,
        completion_tokens=50,
        cache_read_tokens=0,
        cache_write_tokens=0,
        context_window=8000,
        response_id="resp1",
    )

    service2 = "service2"
    metrics2 = Metrics(model_name="gpt-3.5")
    metrics2.add_cost(0.02)
    metrics2.add_token_usage(
        prompt_tokens=200,
        completion_tokens=100,
        cache_read_tokens=0,
        cache_write_tokens=0,
        context_window=4000,
        response_id="resp2",
    )

    conversation_stats.usage_to_metrics[service1] = metrics1
    conversation_stats.usage_to_metrics[service2] = metrics2

    # Get combined metrics
    combined = conversation_stats.get_combined_metrics()

    # Verify combined metrics
    assert combined.accumulated_cost == 0.07  # 0.05 + 0.02
    assert combined.accumulated_token_usage.prompt_tokens == 300  # 100 + 200
    assert combined.accumulated_token_usage.completion_tokens == 150  # 50 + 100
    assert (
        combined.accumulated_token_usage.context_window == 8000
    )  # max of 8000 and 4000


def test_get_metrics_for_usage(conversation_stats):
    """Test that metrics for a specific usage are retrieved correctly."""
    # Add a usage with metrics
    usage_id = "test-usage"
    metrics = Metrics(model_name="gpt-4")
    metrics.add_cost(0.05)
    conversation_stats.usage_to_metrics[usage_id] = metrics

    # Get metrics for the usage
    retrieved_metrics = conversation_stats.get_metrics_for_usage(usage_id)

    # Verify metrics
    assert retrieved_metrics.accumulated_cost == 0.05
    assert retrieved_metrics is metrics  # Should be the same object

    # Test getting metrics for non-existent usage
    # Use a specific exception message pattern instead of a blind Exception
    with pytest.raises(Exception, match="LLM usage does not exist"):
        conversation_stats.get_metrics_for_usage("non-existent-usage")


def test_register_llm_with_new_usage(conversation_stats):
    """Test registering a new LLM usage."""
    # Patch the LLM class to avoid actual API calls
    with patch("openhands.sdk.llm.llm.litellm_completion"):
        llm = LLM(
            usage_id="new-service",
            model="gpt-4o",
            api_key=SecretStr("test_key"),
            num_retries=2,
            retry_min_wait=1,
            retry_max_wait=2,
        )

        # Create a registry event
        service_id = "new-service"
        event = RegistryEvent(llm=llm)

        # Register the LLM
        conversation_stats.register_llm(event)

        # Verify the service was registered
        assert service_id in conversation_stats.usage_to_metrics
        assert conversation_stats.usage_to_metrics[service_id] is llm.metrics


def test_register_llm_with_restored_metrics(conversation_stats):
    """Test registering an LLM usage with restored metrics."""
    # Create restored metrics
    service_id = "restored-service"
    restored_metrics = Metrics(model_name="gpt-4")
    restored_metrics.add_cost(0.1)
    conversation_stats.usage_to_metrics = {service_id: restored_metrics}

    # Patch the LLM class to avoid actual API calls
    with patch("openhands.sdk.llm.llm.litellm_completion"):
        llm = LLM(
            usage_id=service_id,
            model="gpt-4o",
            api_key=SecretStr("test_key"),
            num_retries=2,
            retry_min_wait=1,
            retry_max_wait=2,
        )

        # Create a registry event
        event = RegistryEvent(llm=llm)

        # Register the LLM
        conversation_stats.register_llm(event)

        # Verify the service was registered with restored metrics
        assert service_id in conversation_stats.usage_to_metrics
        assert conversation_stats.usage_to_metrics[service_id] is llm.metrics
        assert llm.metrics is not None
        assert llm.metrics.accumulated_cost == 0.1  # Restored cost

        assert service_id in conversation_stats._restored_usage_ids


def test_llm_registry_notifications(connected_registry_and_stats):
    """Test that LLM registry notifications update usage metrics."""
    mock_llm_registry, conversation_stats = connected_registry_and_stats

    # Create a new LLM through the registry
    service_id = "test-service"

    # Create LLM directly
    llm = LLM(
        usage_id=service_id,
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
    )

    # Add LLM to registry (this should trigger the notification)
    mock_llm_registry.add(llm)

    # Verify the service was registered in conversation stats
    assert service_id in conversation_stats.usage_to_metrics
    assert conversation_stats.usage_to_metrics[service_id] is llm.metrics

    # Add some metrics to the LLM
    assert llm.metrics is not None
    llm.metrics.add_cost(0.05)
    llm.metrics.add_token_usage(
        prompt_tokens=100,
        completion_tokens=50,
        cache_read_tokens=0,
        cache_write_tokens=0,
        context_window=8000,
        response_id="resp1",
    )

    # Verify the metrics are reflected in conversation stats
    assert conversation_stats.usage_to_metrics[service_id].accumulated_cost == 0.05
    assert (
        conversation_stats.usage_to_metrics[
            service_id
        ].accumulated_token_usage.prompt_tokens
        == 100
    )
    assert (
        conversation_stats.usage_to_metrics[
            service_id
        ].accumulated_token_usage.completion_tokens
        == 50
    )

    # Get combined metrics and verify
    combined = conversation_stats.get_combined_metrics()
    assert combined.accumulated_cost == 0.05
    assert combined.accumulated_token_usage.prompt_tokens == 100
    assert combined.accumulated_token_usage.completion_tokens == 50


def test_multiple_llm_usages(connected_registry_and_stats):
    """Test tracking metrics for multiple LLM usages."""
    mock_llm_registry, conversation_stats = connected_registry_and_stats

    # Create multiple LLMs through the registry
    service1 = "service1"
    service2 = "service2"

    # Create LLMs directly
    llm1 = LLM(
        usage_id=service1,
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
    )

    llm2 = LLM(
        usage_id=service2,
        model="gpt-3.5-turbo",
        api_key=SecretStr("test_key"),
        num_retries=2,
        retry_min_wait=1,
        retry_max_wait=2,
    )

    # Add LLMs to registry (this should trigger notifications)
    mock_llm_registry.add(llm1)
    mock_llm_registry.add(llm2)

    # Add different metrics to each LLM
    assert llm1.metrics is not None
    llm1.metrics.add_cost(0.05)
    llm1.metrics.add_token_usage(
        prompt_tokens=100,
        completion_tokens=50,
        cache_read_tokens=0,
        cache_write_tokens=0,
        context_window=8000,
        response_id="resp1",
    )

    assert llm2.metrics is not None
    llm2.metrics.add_cost(0.02)
    llm2.metrics.add_token_usage(
        prompt_tokens=200,
        completion_tokens=100,
        cache_read_tokens=0,
        cache_write_tokens=0,
        context_window=4000,
        response_id="resp2",
    )

    # Verify services were registered in conversation stats
    assert service1 in conversation_stats.usage_to_metrics
    assert service2 in conversation_stats.usage_to_metrics

    # Verify individual metrics
    assert conversation_stats.usage_to_metrics[service1].accumulated_cost == 0.05
    assert conversation_stats.usage_to_metrics[service2].accumulated_cost == 0.02

    # Get combined metrics and verify
    combined = conversation_stats.get_combined_metrics()
    assert combined.accumulated_cost == 0.07  # 0.05 + 0.02
    assert combined.accumulated_token_usage.prompt_tokens == 300  # 100 + 200
    assert combined.accumulated_token_usage.completion_tokens == 150  # 50 + 100
    assert (
        combined.accumulated_token_usage.context_window == 8000
    )  # max of 8000 and 4000


def test_register_llm_with_multiple_restored_usage_ids(conversation_stats):
    """
    Test that reproduces the bug where del self.restored_metrics
    deletes entire dict instead of specific service.
    """

    # Create restored metrics for multiple usages
    service_id_1 = "service-1"
    service_id_2 = "service-2"

    restored_metrics_1 = Metrics(model_name="gpt-4")
    restored_metrics_1.add_cost(0.1)

    restored_metrics_2 = Metrics(model_name="gpt-3.5")
    restored_metrics_2.add_cost(0.05)

    # Set up restored metrics for both services
    conversation_stats.usage_to_metrics = {
        service_id_1: restored_metrics_1,
        service_id_2: restored_metrics_2,
    }

    # Patch the LLM class to avoid actual API calls
    with patch("openhands.sdk.llm.llm.litellm_completion"):
        # Register first LLM
        llm_1 = LLM(
            usage_id=service_id_1,
            model="gpt-4o",
            api_key=SecretStr("test_key"),
            num_retries=2,
            retry_min_wait=1,
            retry_max_wait=2,
        )
        event_1 = RegistryEvent(llm=llm_1)
        conversation_stats.register_llm(event_1)

        # Verify first service was registered with restored metrics
        assert service_id_1 in conversation_stats.usage_to_metrics
        assert llm_1.metrics is not None
        assert llm_1.metrics.accumulated_cost == 0.1

        # After registering first service,
        # restored_metrics should still not contain service_id_2
        assert service_id_2 not in conversation_stats._restored_usage_ids

        # Register second LLM - this should also work with restored metrics
        llm_2 = LLM(
            usage_id=service_id_2,
            model="gpt-3.5-turbo",
            api_key=SecretStr("test_key"),
            num_retries=2,
            retry_min_wait=1,
            retry_max_wait=2,
        )
        event_2 = RegistryEvent(llm=llm_2)
        conversation_stats.register_llm(event_2)

        # Verify second service was registered with restored metrics
        assert service_id_2 in conversation_stats.usage_to_metrics
        assert llm_2.metrics is not None
        assert llm_2.metrics.accumulated_cost == 0.05

        # After both services are marked restored
        assert service_id_2 in conversation_stats._restored_usage_ids
        assert len(conversation_stats._restored_usage_ids) == 2
