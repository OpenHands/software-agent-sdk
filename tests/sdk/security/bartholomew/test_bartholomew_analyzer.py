"""Unit tests for BartholomewASTAnalyzer in OpenHands SDK."""

import pytest
from unittest.mock import MagicMock

from openhands.sdk.event import ActionEvent
from openhands.sdk.security.bartholomew import BartholomewASTAnalyzer
from openhands.sdk.security.risk import SecurityRisk


def _make_mock_action(command_str: str, tool_name: str = "terminal") -> ActionEvent:
    action = MagicMock(spec=ActionEvent)
    action.tool_name = tool_name
    action.thought = []
    action.reasoning_content = None
    action.thinking_blocks = []
    action.responses_reasoning_item = None

    # Mock action object with command attribute or dict representation
    mock_payload = MagicMock()
    mock_payload.command = command_str
    mock_payload.model_dump.return_value = {"command": command_str}
    action.action = mock_payload
    return action


def test_bartholomew_permits_safe_command():
    analyzer = BartholomewASTAnalyzer()
    action = _make_mock_action("git status && ls -la")
    analysis = analyzer.analyze(action)

    assert analysis.risk == SecurityRisk.LOW
    assert analysis.details["decision"] == "PERMIT"
    assert "merkle_receipt" in analysis.details
    assert analysis.details["latency_us"] < 2000.0  # Well under microsecond threshold


def test_bartholomew_vetoes_destructive_shell():
    analyzer = BartholomewASTAnalyzer()
    action = _make_mock_action("rm -rf /")
    analysis = analyzer.analyze(action)

    assert analysis.risk == SecurityRisk.HIGH
    assert analysis.details["decision"] == "VETO"
    assert "rm_rf" in analysis.details["reason"]
    assert "merkle_receipt" in analysis.details


def test_bartholomew_vetoes_ast_syscall():
    analyzer = BartholomewASTAnalyzer()
    action = _make_mock_action("import os; os.system('cat /etc/passwd')")
    analysis = analyzer.analyze(action)

    assert analysis.risk == SecurityRisk.HIGH
    assert analysis.details["decision"] == "VETO"
    assert "system()" in analysis.details["reason"]


def test_bartholomew_vetoes_eval_call():
    analyzer = BartholomewASTAnalyzer()
    action = _make_mock_action("eval('__import__(\"os\").remove(\"file.txt\")')")
    analysis = analyzer.analyze(action)

    assert analysis.risk == SecurityRisk.HIGH
    assert analysis.details["decision"] == "VETO"
    assert "eval()" in analysis.details["reason"]


def test_bartholomew_generates_merkle_receipt():
    analyzer = BartholomewASTAnalyzer()
    action = _make_mock_action("echo 'hello world'")
    analysis = analyzer.analyze(action)

    receipt = analysis.details.get("merkle_receipt")
    assert receipt is not None
    assert len(receipt) == 64  # SHA-256 hex string
