"""Tests for SecurityAnalyzerConfigurationEvent."""

import pytest

from openhands.sdk.event.llm_convertible import ActionEvent
from openhands.sdk.event.security_analyzer import SecurityAnalyzerConfigurationEvent
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer
from openhands.sdk.security.risk import SecurityRisk


class MockSecurityAnalyzer(SecurityAnalyzerBase):
    """Mock security analyzer for testing."""

    def security_risk(self, action: ActionEvent) -> SecurityRisk:
        return SecurityRisk.LOW


def test_security_analyzer_configuration_event_with_analyzer():
    """Test SecurityAnalyzerConfigurationEvent with a configured analyzer."""
    analyzer = MockSecurityAnalyzer()
    event = SecurityAnalyzerConfigurationEvent.from_analyzer(analyzer=analyzer)

    assert event.analyzer_type == "MockSecurityAnalyzer"
    assert event.source == "agent"
    assert "MockSecurityAnalyzer configured" in str(event)


def test_security_analyzer_configuration_event_with_llm_analyzer():
    """Test SecurityAnalyzerConfigurationEvent with LLMSecurityAnalyzer."""
    analyzer = LLMSecurityAnalyzer()
    event = SecurityAnalyzerConfigurationEvent.from_analyzer(analyzer=analyzer)

    assert event.analyzer_type == "LLMSecurityAnalyzer"
    assert event.source == "agent"
    assert "LLMSecurityAnalyzer configured" in str(event)


def test_security_analyzer_configuration_event_without_analyzer():
    """Test SecurityAnalyzerConfigurationEvent without a configured analyzer."""
    event = SecurityAnalyzerConfigurationEvent.from_analyzer(analyzer=None)

    assert event.analyzer_type is None
    assert event.source == "agent"
    assert "No security analyzer configured" in str(event)


def test_security_analyzer_configuration_event_default():
    """Test SecurityAnalyzerConfigurationEvent with default parameters."""
    event = SecurityAnalyzerConfigurationEvent()

    assert event.analyzer_type is None
    assert event.source == "agent"
    assert "No security analyzer configured" in str(event)


def test_security_analyzer_configuration_event_visualize_with_analyzer():
    """Test visualization of SecurityAnalyzerConfigurationEvent with analyzer."""
    analyzer = MockSecurityAnalyzer()
    event = SecurityAnalyzerConfigurationEvent.from_analyzer(analyzer=analyzer)

    visualization = event.visualize
    assert "Security Analyzer Configuration" in str(visualization)
    assert "MockSecurityAnalyzer" in str(visualization)


def test_security_analyzer_configuration_event_visualize_without_analyzer():
    """Test visualization of SecurityAnalyzerConfigurationEvent without analyzer."""
    event = SecurityAnalyzerConfigurationEvent.from_analyzer(analyzer=None)

    visualization = event.visualize
    assert "Security Analyzer Configuration" in str(visualization)
    assert "None (not configured)" in str(visualization)


def test_security_analyzer_configuration_event_immutability():
    """Test that SecurityAnalyzerConfigurationEvent is immutable."""
    analyzer = MockSecurityAnalyzer()
    event = SecurityAnalyzerConfigurationEvent.from_analyzer(analyzer=analyzer)

    # Should not be able to modify the event after creation
    with pytest.raises(Exception):  # Pydantic frozen model raises ValidationError
        event.analyzer_type = "DifferentAnalyzer"


def test_security_analyzer_configuration_event_serialization():
    """Test that SecurityAnalyzerConfigurationEvent can be serialized."""
    analyzer = MockSecurityAnalyzer()
    event = SecurityAnalyzerConfigurationEvent.from_analyzer(analyzer=analyzer)

    # Should be able to serialize to dict
    event_dict = event.model_dump()
    assert event_dict["analyzer_type"] == "MockSecurityAnalyzer"
    assert event_dict["source"] == "agent"
    assert "id" in event_dict
    assert "timestamp" in event_dict

    # Should be able to deserialize from dict
    recreated_event = SecurityAnalyzerConfigurationEvent.model_validate(event_dict)
    assert recreated_event.analyzer_type == event.analyzer_type
    assert recreated_event.source == event.source
    assert recreated_event.id == event.id
