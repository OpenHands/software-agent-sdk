"""Tests for the LLMSecurityAnalyzer class."""

import pytest

from openhands.sdk.event import ActionEvent
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer
from openhands.sdk.security.risk import SecurityRisk
from openhands.sdk.tool import Action


class LlmSecurityAnalyzerMockAction(Action):
    """Mock action for testing."""

    command: str = "test_command"


def create_mock_action_event(
    action: Action, security_risk: SecurityRisk
) -> ActionEvent:
    """Helper to create ActionEvent for testing."""
    return ActionEvent(
        thought=[TextContent(text="test thought")],
        action=action,
        tool_name="test_tool",
        tool_call_id="test_call_id",
        tool_call=MessageToolCall(
            id="test_call_id",
            name="test_tool",
            arguments='{"command": "test"}',
            origin="completion",
        ),
        llm_response_id="test_response_id",
        security_risk=security_risk,
    )


@pytest.mark.parametrize(
    "risk_level",
    [
        SecurityRisk.UNKNOWN,
        SecurityRisk.LOW,
        SecurityRisk.MEDIUM,
        SecurityRisk.HIGH,
    ],
)
def test_llm_security_analyzer_returns_stored_risk(risk_level: SecurityRisk):
    """Test that LLMSecurityAnalyzer returns the security_risk stored in the action event."""  # noqa: E501
    analyzer = LLMSecurityAnalyzer()
    action = LlmSecurityAnalyzerMockAction(command="test")
    action_event = create_mock_action_event(action, risk_level)

    result = analyzer.security_risk(action_event)

    assert result == risk_level
