"""Tests for EnsembleSecurityAnalyzer fusion logic."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from openhands.sdk.event import ActionEvent
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.confirmation_policy import ConfirmRisky
from openhands.sdk.security.defense_in_depth.ensemble import EnsembleSecurityAnalyzer
from openhands.sdk.security.risk import SecurityRisk


# ---------------------------------------------------------------------------
# Test doubles (module-level for DiscriminatedUnionMixin compatibility)
# ---------------------------------------------------------------------------


class FixedRiskTestAnalyzer(SecurityAnalyzerBase):
    """Returns a fixed risk regardless of input."""

    fixed_risk: SecurityRisk = SecurityRisk.LOW

    def security_risk(self, action: ActionEvent) -> SecurityRisk:
        return self.fixed_risk


class FailingTestAnalyzer(SecurityAnalyzerBase):
    """Always raises RuntimeError."""

    def security_risk(self, action: ActionEvent) -> SecurityRisk:
        raise RuntimeError("Analyzer failed")


def make_action(command: str) -> ActionEvent:
    return ActionEvent(
        thought=[TextContent(text="test")],
        tool_name="bash",
        tool_call_id="test",
        tool_call=MessageToolCall(
            id="test",
            name="bash",
            arguments=json.dumps({"command": command}),
            origin="completion",
        ),
        llm_response_id="test",
    )


# ---------------------------------------------------------------------------
# Ensemble tests
# ---------------------------------------------------------------------------


class TestEnsemble:
    """Max-severity fusion, fail-closed, UNKNOWN handling."""

    def test_max_severity_low_low(self):
        ensemble = EnsembleSecurityAnalyzer(
            analyzers=[
                FixedRiskTestAnalyzer(fixed_risk=SecurityRisk.LOW),
                FixedRiskTestAnalyzer(fixed_risk=SecurityRisk.LOW),
            ],
        )
        assert ensemble.security_risk(make_action("test")) == SecurityRisk.LOW

    def test_max_severity_low_high(self):
        ensemble = EnsembleSecurityAnalyzer(
            analyzers=[
                FixedRiskTestAnalyzer(fixed_risk=SecurityRisk.LOW),
                FixedRiskTestAnalyzer(fixed_risk=SecurityRisk.HIGH),
            ],
        )
        assert ensemble.security_risk(make_action("test")) == SecurityRisk.HIGH

    def test_max_severity_medium_high(self):
        ensemble = EnsembleSecurityAnalyzer(
            analyzers=[
                FixedRiskTestAnalyzer(fixed_risk=SecurityRisk.MEDIUM),
                FixedRiskTestAnalyzer(fixed_risk=SecurityRisk.HIGH),
            ],
        )
        assert ensemble.security_risk(make_action("test")) == SecurityRisk.HIGH

    def test_fail_closed_on_exception(self):
        ensemble = EnsembleSecurityAnalyzer(
            analyzers=[FailingTestAnalyzer()],
        )
        risk = ensemble.security_risk(make_action("anything"))
        assert risk == SecurityRisk.HIGH
        assert ConfirmRisky().should_confirm(risk) is True

    def test_unknown_plus_high(self):
        ensemble = EnsembleSecurityAnalyzer(
            analyzers=[
                FixedRiskTestAnalyzer(fixed_risk=SecurityRisk.UNKNOWN),
                FixedRiskTestAnalyzer(fixed_risk=SecurityRisk.HIGH),
            ],
        )
        assert ensemble.security_risk(make_action("test")) == SecurityRisk.HIGH

    def test_unknown_plus_low(self):
        ensemble = EnsembleSecurityAnalyzer(
            analyzers=[
                FixedRiskTestAnalyzer(fixed_risk=SecurityRisk.UNKNOWN),
                FixedRiskTestAnalyzer(fixed_risk=SecurityRisk.LOW),
            ],
        )
        assert ensemble.security_risk(make_action("test")) == SecurityRisk.LOW

    def test_all_unknown_propagated(self):
        ensemble = EnsembleSecurityAnalyzer(
            analyzers=[
                FixedRiskTestAnalyzer(fixed_risk=SecurityRisk.UNKNOWN),
                FixedRiskTestAnalyzer(fixed_risk=SecurityRisk.UNKNOWN),
            ],
        )
        assert ensemble.security_risk(make_action("test")) == SecurityRisk.UNKNOWN

    def test_single_analyzer(self):
        ensemble = EnsembleSecurityAnalyzer(
            analyzers=[FixedRiskTestAnalyzer(fixed_risk=SecurityRisk.MEDIUM)],
        )
        assert ensemble.security_risk(make_action("test")) == SecurityRisk.MEDIUM

    def test_empty_analyzers_rejected(self):
        with pytest.raises(ValidationError):
            EnsembleSecurityAnalyzer(analyzers=[])
