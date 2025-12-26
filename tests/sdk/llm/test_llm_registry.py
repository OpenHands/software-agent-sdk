from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.llm_registry import LLMRegistry, RegistryEvent
from openhands.sdk.llm.profiles.config import ProfileMetadata, ProfilesConfig
from openhands.sdk.llm.profiles.manager import ProfileManager


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
        assert service_id in registry._usage_to_llm
        assert registry._usage_to_llm[service_id] is mock_llm

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


def get_config() -> ProfilesConfig:
    return ProfilesConfig(
        default_profile="profile_1",
        profiles=[
            ProfileMetadata(
                name="profile_1",
                file="profile_1.json",
                description="use for translation",
                usage_id="agent_1",
            ),
            ProfileMetadata(
                name="profile_2",
                file="profile_2.json",
                description="use for fun",
                usage_id="agent_2",
            ),
        ],
    )


def get_profiles() -> list[dict[str, Any]]:
    return [
        {
            "model": "profile_1",
            "api_key": "...",
            "base_url": "asd",
            "usage_id": "agent_1",
            "litellm_extra_body": {},
            "OVERRIDE_ON_SERIALIZE": [
                "api_key",
                "aws_access_key_id",
                "aws_secret_access_key",
                "litellm_extra_body",
            ],
        },
        {
            "model": "profile_2",
            "api_key": "...",
            "base_url": "asd",
            "usage_id": "agent_2",
            "litellm_extra_body": {},
            "OVERRIDE_ON_SERIALIZE": [
                "api_key",
                "aws_access_key_id",
                "aws_secret_access_key",
                "litellm_extra_body",
            ],
        },
    ]


@pytest.fixture(scope="function")
def profiles_base_dir(tmp_path_factory) -> Path:
    """The fixture set up a directory with some profiles inside."""
    base_dir = tmp_path_factory.mktemp("tmp_profile_factory")

    config = get_config()
    config_path = base_dir / "config.json"
    config_path.write_text(json.dumps(config.model_dump()))

    profiles = get_profiles()
    for p_metadata, p in zip(config.profiles, profiles):
        file_path = base_dir / p_metadata.file
        Path(file_path).write_text(json.dumps(p))

    return base_dir


def get_llm_instances() -> list[LLM]:
    return [
        LLM(
            model="llm_1",
            api_key="SOME_HIDDEN_API_KEY",
            base_url="asd",
            usage_id="llm_agent_1",
        ),
        LLM(
            model="llm_2",
            api_key="SOME_HIDDEN_API_KEY",
            base_url="asd",
            usage_id="llm_agent_2",
        ),
    ]


def test_registry_export_profiles(tmp_path_factory) -> None:
    registry = LLMRegistry()
    assert registry.profile_manager is None

    llm_1, llm_2 = get_llm_instances()
    registry.add(llm_1)
    registry.add(llm_2)

    assert registry.list_usage_ids() == ["llm_agent_1", "llm_agent_2"]

    export_path = tmp_path_factory.mktemp("tmp_profile_factory")
    registry.export_profiles(export_path)

    # map file name to path to file
    # useful to do some check
    exported_file_map = {p.name: p for p in list(export_path.iterdir())}
    # test that the profiles associated to the registry were
    # exported with the correct names
    assert len(exported_file_map) == 3
    assert "config.json" in exported_file_map
    assert "llm_1.json" in exported_file_map
    assert "llm_2.json" in exported_file_map

    # check the content of the config
    config = ProfilesConfig.from_json(exported_file_map["config.json"])
    assert config.default_profile is None
    assert len(config.profiles) == 2
    for j, profile_metadata in enumerate(config.profiles, 1):
        assert profile_metadata.name == f"llm_{j}"
        assert profile_metadata.file == f"llm_{j}.json"
        assert profile_metadata.usage_id == f"llm_agent_{j}"
        assert profile_metadata.description == f"llm_agent_{j}"

    # check that the saved profiles correspond to the llm_instances
    profile_1 = LLM.load_from_json(str(exported_file_map["llm_1.json"]))
    assert profile_1 == llm_1
    profile_2 = LLM.load_from_json(str(exported_file_map["llm_2.json"]))
    assert profile_2 == llm_2


def test_registry_behaviour_with_disk(profiles_base_dir) -> None:
    registry = LLMRegistry(
        load_from_disk=True,
        profile_manager_path=profiles_base_dir,
    )
    assert isinstance(registry.profile_manager, ProfileManager)

    # check that we can also see the usage ids on disk
    assert registry.list_usage_ids() == ["agent_1", "agent_2"]

    # check that we cannot add usage_id present already on disk
    duplicate_llm = LLM(
        model="llm_1",
        api_key="SOME_HIDDEN_API_KEY",
        base_url="asd",
        usage_id="agent_1",
    )
    with pytest.raises(ValueError):
        registry.add(duplicate_llm)

    # check if fetching from disk happens correctly
    for j in range(1, 3):
        agent = registry.get(f"agent_{j}")
        assert agent.usage_id == f"agent_{j}"
        assert agent.model == f"profile_{j}"
    # once loaded, they are stored in memory
    assert sorted(registry._usage_to_llm.keys()) == ["agent_1", "agent_2"]

    llm_1, llm_2 = get_llm_instances()
    registry.add(llm_1)
    registry.add(llm_2)
    assert registry.list_usage_ids() == [
        "agent_1",
        "agent_2",
        "llm_agent_1",
        "llm_agent_2",
    ]

    registry.export_profiles()

    file_to_path = {p.name: p for p in list(profiles_base_dir.iterdir())}

    assert len(file_to_path) == 5
    assert "config.json" in file_to_path
    assert "llm_1.json" in file_to_path
    assert "llm_2.json" in file_to_path
    assert "profile_1.json" in file_to_path
    assert "profile_2.json" in file_to_path
