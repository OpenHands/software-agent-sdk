"""Tests for the SecurityAnalyzer class."""

from pydantic import Field

from openhands.sdk.event import ActionEvent, PauseEvent
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.risk import SecurityRisk
from openhands.sdk.tool import Action


class SecurityAnalyzerMockAction(Action):
    """Mock action for testing."""

    command: str = "test_command"


class SecurityAnalyzer(SecurityAnalyzerBase):
    """Test implementation of SecurityAnalyzer with controllable security_risk
    method.
    """

    risk_return_value: SecurityRisk = SecurityRisk.LOW
    security_risk_calls: list[ActionEvent] = Field(default_factory=list)
    handle_api_request_calls: list[dict] = Field(default_factory=list)
    close_calls: list[bool] = Field(default_factory=list)

    def security_risk(self, action: ActionEvent) -> SecurityRisk:
        """Return configurable risk level for testing."""
        self.security_risk_calls.append(action)
        return self.risk_return_value

    def handle_api_request(self, request_data: dict) -> dict:
        """Mock implementation - not tested as it's going away."""
        self.handle_api_request_calls.append(request_data)
        return {"status": "ok"}

    def close(self) -> None:
        """Mock implementation - not tested as it's going away."""
        self.close_calls.append(True)


def create_mock_action_event(action: Action) -> ActionEvent:
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
    )


def test_analyze_event_with_action_event():
    """Test analyze_event with ActionEvent returns security risk."""
    analyzer = SecurityAnalyzer(risk_return_value=SecurityRisk.MEDIUM)
    action = SecurityAnalyzerMockAction(command="test")
    action_event = create_mock_action_event(action)

    result = analyzer.analyze_event(action_event)

    assert result == SecurityRisk.MEDIUM
    assert len(analyzer.security_risk_calls) == 1
    assert analyzer.security_risk_calls[0] == action_event


def test_analyze_event_with_non_action_event():
    """Test analyze_event with non-ActionEvent returns None."""
    analyzer = SecurityAnalyzer(risk_return_value=SecurityRisk.HIGH)

    result = analyzer.analyze_event(PauseEvent())

    assert result is None
    assert len(analyzer.security_risk_calls) == 0


def test_analyze_pending_actions_success():
    """Test analyze_pending_actions with successful analysis."""
    analyzer = SecurityAnalyzer(risk_return_value=SecurityRisk.MEDIUM)

    action1 = SecurityAnalyzerMockAction(command="action1")
    action2 = SecurityAnalyzerMockAction(command="action2")
    action_event1 = create_mock_action_event(action1)
    action_event2 = create_mock_action_event(action2)

    pending_actions = [action_event1, action_event2]

    result = analyzer.analyze_pending_actions(pending_actions)

    assert len(result) == 2
    assert result[0] == (action_event1, SecurityRisk.MEDIUM)
    assert result[1] == (action_event2, SecurityRisk.MEDIUM)
    assert len(analyzer.security_risk_calls) == 2


def test_analyze_pending_actions_empty_list():
    """Test analyze_pending_actions with empty list."""
    analyzer = SecurityAnalyzer(risk_return_value=SecurityRisk.LOW)

    result = analyzer.analyze_pending_actions([])

    assert result == []
    assert len(analyzer.security_risk_calls) == 0


def test_analyze_pending_actions_with_exception():
    """Test analyze_pending_actions handles exceptions by defaulting to HIGH risk."""

    class FailingAnalyzer(SecurityAnalyzer):
        def security_risk(self, action: ActionEvent) -> SecurityRisk:
            super().security_risk(action)  # Record the call
            raise ValueError("Analysis failed")

    analyzer = FailingAnalyzer()
    action = SecurityAnalyzerMockAction(command="failing_action")
    action_event = create_mock_action_event(action)

    result = analyzer.analyze_pending_actions([action_event])

    assert len(result) == 1
    assert result[0] == (action_event, SecurityRisk.HIGH)
    assert len(analyzer.security_risk_calls) == 1


def test_analyze_pending_actions_mixed_risks() -> None:
    """Test analyze_pending_actions with different risk levels."""

    class VariableRiskAnalyzer(SecurityAnalyzer):
        call_count: int = 0
        risks: list[SecurityRisk] = Field(
            default_factory=lambda: [
                SecurityRisk.LOW,
                SecurityRisk.HIGH,
                SecurityRisk.MEDIUM,
            ]
        )

        def security_risk(self, action: ActionEvent) -> SecurityRisk:
            risk = self.risks[self.call_count % len(self.risks)]
            self.call_count += 1
            return risk

    analyzer = VariableRiskAnalyzer()

    actions = [SecurityAnalyzerMockAction(command=f"action{i}") for i in range(3)]
    action_events = [create_mock_action_event(action) for action in actions]

    result = analyzer.analyze_pending_actions(action_events)

    assert len(result) == 3
    assert result[0][1] == SecurityRisk.LOW
    assert result[1][1] == SecurityRisk.HIGH
    assert result[2][1] == SecurityRisk.MEDIUM


def test_analyze_pending_actions_partial_failure():
    """Test analyze_pending_actions with some actions failing analysis."""

    class PartiallyFailingAnalyzer(SecurityAnalyzer):
        def security_risk(self, action: ActionEvent) -> SecurityRisk:
            # In general not needed, but the test security analyzer is also recording
            # all the calls for testing purposes and this ensures we keep that behavior
            super().security_risk(action)

            assert hasattr(action.action, "command")
            if getattr(action.action, "command") == "failing_action":
                raise RuntimeError("Specific action failed")
            return SecurityRisk.LOW

    analyzer = PartiallyFailingAnalyzer()

    action1 = SecurityAnalyzerMockAction(command="good_action")
    action2 = SecurityAnalyzerMockAction(command="failing_action")
    action3 = SecurityAnalyzerMockAction(command="another_good_action")

    action_events = [
        create_mock_action_event(action1),
        create_mock_action_event(action2),
        create_mock_action_event(action3),
    ]

    result = analyzer.analyze_pending_actions(action_events)

    assert len(result) == 3
    assert result[0][1] == SecurityRisk.LOW
    assert result[1][1] == SecurityRisk.HIGH  # Failed analysis defaults to HIGH
    assert result[2][1] == SecurityRisk.LOW
    assert len(analyzer.security_risk_calls) == 3
