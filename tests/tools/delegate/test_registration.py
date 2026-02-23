"""Tests for the delegate agent registration utilities."""

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.subagent.registration import (
    _agent_definition_to_factory,
    _reset_registry_for_tests,
    get_agent_factory,
    register_agent,
    register_agent_if_absent,
)
from openhands.sdk.subagent.schema import AgentDefinition


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


def test_register_agent_if_absent_new() -> None:
    """register_agent_if_absent returns True for new agents."""

    def dummy_factory(llm: LLM) -> Agent:  # type: ignore[unused-argument]
        return cast(Agent, MagicMock())

    result = register_agent_if_absent(
        name="new_agent",
        factory_func=dummy_factory,
        description="New agent",
    )
    assert result is True

    factory = get_agent_factory("new_agent")
    assert factory.description == "New agent"


def test_register_agent_if_absent_existing() -> None:
    """register_agent_if_absent returns False for existing agents."""

    def factory1(llm: LLM) -> Agent:  # type: ignore[unused-argument]
        return cast(Agent, MagicMock())

    def factory2(llm: LLM) -> Agent:  # type: ignore[unused-argument]
        return cast(Agent, MagicMock())

    register_agent(name="dup_agent", factory_func=factory1, description="First")

    result = register_agent_if_absent(
        name="dup_agent",
        factory_func=factory2,
        description="Second",
    )
    assert result is False

    # First registration should be preserved
    factory = get_agent_factory("dup_agent")
    assert factory.description == "First"


def test_end_to_end_md_to_factory_to_registry(tmp_path: Path) -> None:
    """End-to-end: .md file -> AgentDefinition.load() -> factory -> register -> get."""
    md_file = tmp_path / "test-agent.md"
    md_file.write_text(
        "---\n"
        "name: e2e-test-agent\n"
        "description: End-to-end test agent\n"
        "model: inherit\n"
        "tools:\n"
        "  - ReadTool\n"
        "  - GrepTool\n"
        "---\n\n"
        "You are a test agent for end-to-end testing.\n"
        "Focus on correctness and clarity.\n"
    )

    # Load from file
    agent_def = AgentDefinition.load(md_file)
    assert agent_def.name == "e2e-test-agent"
    assert agent_def.description == "End-to-end test agent"
    assert agent_def.tools == ["ReadTool", "GrepTool"]

    # Convert to factory
    factory = _agent_definition_to_factory(agent_def)

    # Register
    result = register_agent_if_absent(
        name=agent_def.name,
        factory_func=factory,
        description=agent_def.description,
    )
    assert result is True

    # Retrieve and verify
    retrieved = get_agent_factory("e2e-test-agent")
    assert retrieved.description == "End-to-end test agent"

    # Create agent from factory (with real LLM)
    test_llm = LLM(
        model="gpt-4o",
        api_key=SecretStr("test-key"),
        usage_id="test-llm",
    )
    agent = retrieved.factory_func(test_llm)
    assert isinstance(agent, Agent)
    tool_names = [t.name for t in agent.tools]
    assert "ReadTool" in tool_names
    assert "GrepTool" in tool_names
