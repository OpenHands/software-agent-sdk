"""Regression tests for the HOL Guard SecurityAnalyzerBase example."""

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from openhands.sdk.event import ActionEvent
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.security.risk import SecurityRisk
from openhands.sdk.tool import Action
from openhands.tools.terminal import TerminalAction


EXAMPLE_PATH = (
    Path(__file__).parents[3]
    / "examples"
    / "01_standalone_sdk"
    / "43_hol_guard_security_analyzer.py"
)
spec = importlib.util.spec_from_file_location("hol_guard_security_analyzer", EXAMPLE_PATH)
assert spec is not None and spec.loader is not None
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


class DummyAction(Action):
    value: str = "not-terminal"


def _event(action: Action) -> ActionEvent:
    command = getattr(action, "command", "")
    return ActionEvent(
        thought=[TextContent(text="HOL Guard analyzer test")],
        action=action,
        tool_name="terminal",
        tool_call_id="hol-guard-test",
        tool_call=MessageToolCall(
            id="hol-guard-test",
            name="terminal",
            arguments=json.dumps({"command": command}),
            origin="completion",
        ),
        llm_response_id="hol-guard-test",
    )


def _completed(payload: Any, *, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    stdout = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.CompletedProcess(
        args=["hol-guard"], returncode=returncode, stdout=stdout, stderr=""
    )


def test_analyzer_constructs_as_pydantic_model() -> None:
    analyzer = example.HolGuardSecurityAnalyzer(
        guard_executable="guard-bin",
        workspace="/tmp",
        timeout_seconds=3.5,
    )

    assert analyzer.guard_executable == "guard-bin"
    assert analyzer.workspace == "/tmp"
    assert analyzer.timeout_seconds == 3.5

    with pytest.raises(ValidationError):
        example.HolGuardSecurityAnalyzer(timeout_seconds=0)


def test_explicitly_benign_terminal_command_is_low_risk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return _completed(
            {
                "minimum_action": "allow",
                "classification": {"explicitly_benign": True},
            }
        )

    monkeypatch.setattr(example.subprocess, "run", fake_run)
    analyzer = example.HolGuardSecurityAnalyzer(workspace="/workspace")

    risk = analyzer.security_risk(_event(TerminalAction(command="pwd")))

    assert risk == SecurityRisk.LOW
    assert calls == [
        (
            ["hol-guard", "command", "test", "pwd", "--json"],
            {
                "cwd": "/workspace",
                "capture_output": True,
                "check": False,
                "text": True,
                "timeout": 10.0,
            },
        )
    ]


@pytest.mark.parametrize(
    "minimum_action",
    ["review", "block", "require-reapproval", "sandbox-required"],
)
def test_guarded_minimum_actions_are_high_risk(
    monkeypatch: pytest.MonkeyPatch,
    minimum_action: str,
) -> None:
    monkeypatch.setattr(
        example.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(
            {
                "minimum_action": minimum_action,
                "classification": {"explicitly_benign": True},
            }
        ),
    )

    risk = example.HolGuardSecurityAnalyzer().security_risk(
        _event(TerminalAction(command="rm -rf /tmp/demo"))
    )

    assert risk == SecurityRisk.HIGH


@pytest.mark.parametrize(
    ("result", "action"),
    [
        (_completed("not-json"), TerminalAction(command="pwd")),
        (_completed(["not", "an", "object"]), TerminalAction(command="pwd")),
        (_completed({}, returncode=2), TerminalAction(command="pwd")),
        (
            _completed(
                {
                    "minimum_action": "allow",
                    "classification": {"explicitly_benign": False},
                }
            ),
            TerminalAction(command="pwd"),
        ),
    ],
)
def test_untrusted_or_failed_guard_results_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    result: subprocess.CompletedProcess[str],
    action: TerminalAction,
) -> None:
    monkeypatch.setattr(
        example.subprocess,
        "run",
        lambda *_args, **_kwargs: result,
    )

    risk = example.HolGuardSecurityAnalyzer().security_risk(_event(action))

    assert risk == SecurityRisk.HIGH


def test_missing_guard_executable_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("hol-guard")

    monkeypatch.setattr(example.subprocess, "run", missing)

    risk = example.HolGuardSecurityAnalyzer().security_risk(
        _event(TerminalAction(command="pwd"))
    )

    assert risk == SecurityRisk.HIGH


def test_non_terminal_and_terminal_input_actions_fail_closed() -> None:
    analyzer = example.HolGuardSecurityAnalyzer()

    assert analyzer.security_risk(_event(DummyAction())) == SecurityRisk.HIGH
    assert (
        analyzer.security_risk(_event(TerminalAction(command="C-c", is_input=True)))
        == SecurityRisk.HIGH
    )
