"""Tests for the delegate agent registration utilities."""

from typing import cast
from unittest.mock import MagicMock

import pytest

from openhands.sdk import LLM, Agent
from openhands.tools.delegate.registration import (
    _reset_registry_for_tests,
    get_agent_factory,
    register_agent,
)


def setup_function() -> None:
    _reset_registry_for_tests()


def teardown_function() -> None:
    _reset_registry_for_tests()


def test_default_factory_is_returned_for_empty_type() -> None:
    """Ensure default agent factory is used when no type is provided."""
    default_factory = get_agent_factory(None)
    assert "Default general-purpose agent" in default_factory.description
    assert default_factory == get_agent_factory("default")
    assert default_factory == get_agent_factory("")


def test_register_and_retrieve_custom_agent_factory() -> None:
    """User-registered agent factories should be retrievable by name."""

    def dummy_factory(llm: LLM) -> Agent:  # type: ignore[unused-argument]
        return cast(Agent, MagicMock())

    register_agent(
        name="custom_agent",
        factory_func=dummy_factory,
        description="Custom agent for testing",
    )

    factory = get_agent_factory("custom_agent")
    assert factory.description == "Custom agent for testing"
    assert factory.factory_func is dummy_factory


def test_unknown_agent_type_raises_value_error() -> None:
    """Retrieving an unknown agent type should provide a helpful error."""
    with pytest.raises(ValueError) as excinfo:
        get_agent_factory("missing")

    assert "Unknown agent 'missing'" in str(excinfo.value)
