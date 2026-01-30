"""Tests for Agent._security_service.extract_security_risk method.

This module tests the _extract_security_risk method which handles extraction
and validation of security risk parameters from tool arguments.
"""

import uuid

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.conversation.types import ConversationCallbackType
from openhands.sdk.event import ActionEvent
from openhands.sdk.llm import LLM
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer
from openhands.sdk.security.risk import SecurityRisk
from openhands.sdk.security.security_service import (
    DefaultSecurityService,
)
from openhands.sdk.workspace.local import LocalWorkspace


class MockNonLLMAnalyzer(SecurityAnalyzerBase):
    """Mock security analyzer that is not an LLMSecurityAnalyzer."""

    def security_risk(self, action: ActionEvent) -> SecurityRisk:
        return SecurityRisk.LOW


class MockSecurityServiceAgent(Agent):
    """Mock SecurityService to expose protected methods for testing."""

    def init_state(
        self,
        state: "ConversationState",
        on_event: "ConversationCallbackType",  # noqa: ARG002
    ) -> None:
        """Initialize the empty conversation state to prepare the agent for user
        messages.

        Typically this involves adding system message

        NOTE: state will be mutated in-place.
        """

        self._security_service = DefaultSecurityService(state)


def init_agent(agent: Agent, security_analyzer: SecurityAnalyzerBase | None):
    state = ConversationState.create(
        id=uuid.uuid4(), agent=agent, workspace=LocalWorkspace(working_dir="/tmp")
    )
    state.security_analyzer = security_analyzer
    agent.init_state(state, lambda e: None)


@pytest.fixture
def mock_llm():
    """Create a mock LLM for testing."""
    return LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )


@pytest.fixture
def agent_with_llm_analyzer(mock_llm):
    """Create an agent with LLMSecurityAnalyzer."""
    agent = MockSecurityServiceAgent(llm=mock_llm)
    return agent, LLMSecurityAnalyzer()


@pytest.fixture
def agent_with_non_llm_analyzer(mock_llm):
    """Create an agent with non-LLM security analyzer."""
    agent = MockSecurityServiceAgent(llm=mock_llm)
    return agent, MockNonLLMAnalyzer()


@pytest.fixture
def agent_without_analyzer(mock_llm):
    """Create an agent without security analyzer."""
    agent = MockSecurityServiceAgent(llm=mock_llm)
    return agent, None


@pytest.mark.parametrize(
    "agent_fixture,security_risk_value,expected_result,should_raise",
    [
        # Case 1: LLM analyzer set, security risk passed, extracted properly
        ("agent_with_llm_analyzer", "LOW", SecurityRisk.LOW, False),
        ("agent_with_llm_analyzer", "MEDIUM", SecurityRisk.MEDIUM, False),
        ("agent_with_llm_analyzer", "HIGH", SecurityRisk.HIGH, False),
        ("agent_with_llm_analyzer", "UNKNOWN", SecurityRisk.UNKNOWN, False),
        # Case 2: analyzer is not set, security risk is passed, extracted properly
        ("agent_with_non_llm_analyzer", "LOW", SecurityRisk.LOW, False),
        ("agent_with_non_llm_analyzer", "MEDIUM", SecurityRisk.MEDIUM, False),
        ("agent_with_non_llm_analyzer", "HIGH", SecurityRisk.HIGH, False),
        ("agent_with_non_llm_analyzer", "UNKNOWN", SecurityRisk.UNKNOWN, False),
        ("agent_without_analyzer", "LOW", SecurityRisk.LOW, False),
        ("agent_without_analyzer", "MEDIUM", SecurityRisk.MEDIUM, False),
        ("agent_without_analyzer", "HIGH", SecurityRisk.HIGH, False),
        ("agent_without_analyzer", "UNKNOWN", SecurityRisk.UNKNOWN, False),
        # Case 3: LLM analyzer set, security risk not passed, ValueError raised
        ("agent_with_llm_analyzer", None, None, True),
        # Case 4: analyzer is not set, security risk is not passed, UNKNOWN returned
        ("agent_with_non_llm_analyzer", None, SecurityRisk.UNKNOWN, False),
        ("agent_without_analyzer", None, SecurityRisk.UNKNOWN, False),
        # Case 5: invalid security risk value passed, ValueError raised
        ("agent_with_llm_analyzer", "INVALID", None, True),
        ("agent_with_non_llm_analyzer", "INVALID", None, True),
        ("agent_without_analyzer", "INVALID", None, True),
    ],
)
def test_extract_security_risk(
    request, agent_fixture, security_risk_value, expected_result, should_raise
):
    """Test _extract_security_risk method with various scenarios."""
    # Get the agent fixture
    agent, security_analyzer = request.getfixturevalue(agent_fixture)
    init_agent(agent, security_analyzer)

    # Prepare arguments
    arguments = {"some_param": "value"}
    if security_risk_value is not None:
        arguments["security_risk"] = security_risk_value

    tool_name = "test_tool"

    if should_raise:
        with pytest.raises(ValueError):
            agent._security_service.extract_security_risk(arguments, tool_name, False)
    else:
        result = agent._security_service.extract_security_risk(
            arguments, tool_name, False
        )
        assert result == expected_result

        # Verify that security_risk was popped from arguments
        assert "security_risk" not in arguments
        # Verify other arguments remain
        assert arguments["some_param"] == "value"


def test_extract_security_risk_arguments_mutation():
    """Test that arguments dict is properly mutated (security_risk is popped)."""
    agent = MockSecurityServiceAgent(
        llm=LLM(
            usage_id="test-llm",
            model="test-model",
            api_key=SecretStr("test-key"),
            base_url="http://test",
        )
    )
    init_agent(agent, None)

    # Test with security_risk present
    arguments = {"param1": "value1", "security_risk": "LOW", "param2": "value2"}
    original_args = arguments.copy()

    result = agent._security_service.extract_security_risk(
        arguments, "test_tool", False
    )  # type: ignore

    # Verify result
    assert result == SecurityRisk.LOW

    # Verify security_risk was popped
    assert "security_risk" not in arguments

    # Verify other parameters remain
    assert arguments["param1"] == original_args["param1"]
    assert arguments["param2"] == original_args["param2"]
    assert len(arguments) == 2  # Only 2 params should remain


def test_extract_security_risk_with_empty_arguments():
    """Test _extract_security_risk with empty arguments dict."""
    agent = MockSecurityServiceAgent(
        llm=LLM(
            usage_id="test-llm",
            model="test-model",
            api_key=SecretStr("test-key"),
            base_url="http://test",
        )
    )
    init_agent(agent, None)

    arguments = {}
    result = agent._security_service.extract_security_risk(
        arguments, "test_tool", False
    )  # type: ignore

    # Should return UNKNOWN when no analyzer and no security_risk
    assert result == SecurityRisk.UNKNOWN
    assert arguments == {}  # Should remain empty


def test_extract_security_risk_with_read_only_tool():
    """Test _extract_security_risk with read only tool."""
    agent = MockSecurityServiceAgent(
        llm=LLM(
            usage_id="test-llm",
            model="test-model",
            api_key=SecretStr("test-key"),
            base_url="http://test",
        )
    )
    init_agent(agent, LLMSecurityAnalyzer())

    # Test with readOnlyHint=True - should return UNKNOWN regardless of security_risk
    arguments = {"param1": "value1", "security_risk": "HIGH"}
    result = agent._security_service.extract_security_risk(arguments, "test_tool", True)  # type: ignore

    # Should return UNKNOWN when read_only_tool is True
    assert result == SecurityRisk.UNKNOWN
    # security_risk should still be popped from arguments
    assert "security_risk" not in arguments
    assert arguments["param1"] == "value1"
