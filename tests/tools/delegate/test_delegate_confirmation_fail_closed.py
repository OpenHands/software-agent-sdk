"""Tests for DelegateExecutor fail-closed confirmation (#6999)."""

import json
import warnings
from pathlib import Path
from typing import Any

import pytest

from openhands.sdk import Agent, LocalConversation, Message, TextContent, Tool
from openhands.sdk.event import (
    ActionEvent,
    SecurityAnalysisEvent,
    UserRejectObservation,
)
from openhands.sdk.llm import MessageToolCall
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.confirmation_policy import (
    AlwaysConfirm,
    ConfirmRisky,
    NeverConfirm,
)
from openhands.sdk.security.risk import SecurityRisk
from openhands.sdk.subagent.registry import _reset_registry_for_tests
from openhands.sdk.testing import TestLLM
from openhands.tools.delegate import DelegateExecutor
from openhands.tools.delegate.definition import DelegateAction
from openhands.tools.preset import register_builtins_agents
from openhands.tools.preset.default import register_default_tools


class DelegateCommandKeywordAnalyzer(SecurityAnalyzerBase):
    """Evaluates commands containing 'DANGER' as HIGH risk, others as LOW risk."""

    marker: str = "DANGER"

    def security_risk(self, action: ActionEvent) -> SecurityRisk:
        cmd = getattr(action.action, "command", "")
        if self.marker in cmd:
            return SecurityRisk.HIGH
        return SecurityRisk.LOW


@pytest.fixture(autouse=True)
def _clean_registry():
    _reset_registry_for_tests()
    register_default_tools(enable_browser=False)
    register_builtins_agents(enable_browser=False)
    yield
    _reset_registry_for_tests()


def _cmd_msg(call_id: str, command: str) -> Message:
    return Message(
        role="assistant",
        content=[TextContent(text="")],
        tool_calls=[
            MessageToolCall(
                id=call_id,
                name="terminal",
                arguments=json.dumps({"command": command}),
                origin="completion",
            )
        ],
    )


def _text_msg(text: str) -> Message:
    return Message(role="assistant", content=[TextContent(text=text)])


def test_delegate_executor_fails_closed_under_always_confirm(tmp_path: Path) -> None:
    """DelegateExecutor with default confirmation_handler=None rejects pending
    actions under AlwaysConfirm instead of executing them.
    """
    canary = tmp_path / "delegate_always_confirm.txt"
    llm = TestLLM.from_messages(
        [
            _cmd_msg("call_del_1", f"echo PWNED > {canary}"),
            _text_msg("Rejected as expected."),
        ]
    )
    parent = LocalConversation(
        agent=Agent(llm=llm, tools=[Tool(name="terminal")]),
        workspace=str(tmp_path),
        visualizer=None,
    )
    parent.set_confirmation_policy(AlwaysConfirm())

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        executor = DelegateExecutor()
        executor(
            DelegateAction(
                command="spawn", ids=["worker1"], agent_types=["bash-runner"]
            ),
            parent,
        )
        obs = executor(
            DelegateAction(command="delegate", tasks={"worker1": "Run command"}),
            parent,
        )
        sub_conv = executor._sub_agents["worker1"]
        rejections = [
            ev for ev in sub_conv.state.events if isinstance(ev, UserRejectObservation)
        ]
        assert len(rejections) == 1
        assert "interactive confirmation" in rejections[0].rejection_reason.lower()
        assert "unavailable" in rejections[0].rejection_reason.lower()
        executor.close()

    parent.close()
    assert not canary.exists()
    assert not obs.is_error


def test_delegate_executor_inherits_analyzer_and_blocks_confirm_risky(
    tmp_path: Path,
) -> None:
    """DelegateExecutor._spawn_agents inherits parent.state.security_analyzer,
    records non-UNKNOWN risks in SecurityAnalysisEvent, allows LOW-risk actions,
    and rejects HIGH-risk actions when confirmation_handler is None.
    """
    safe_file = tmp_path / "delegate_safe.txt"
    canary = tmp_path / "delegate_danger.txt"
    llm = TestLLM.from_messages(
        [
            _cmd_msg("call_del_low", f"echo SAFE > {safe_file}"),
            _cmd_msg("call_del_high", f"echo DANGER > {canary}"),
            _text_msg("High-risk action blocked."),
        ]
    )
    analyzer = DelegateCommandKeywordAnalyzer(marker="DANGER")
    parent = LocalConversation(
        agent=Agent(llm=llm, tools=[Tool(name="terminal")]),
        workspace=str(tmp_path),
        visualizer=None,
    )
    parent.set_security_analyzer(analyzer)
    parent.set_confirmation_policy(ConfirmRisky(threshold=SecurityRisk.HIGH))

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        executor = DelegateExecutor()
        executor(
            DelegateAction(
                command="spawn", ids=["worker1"], agent_types=["bash-runner"]
            ),
            parent,
        )
        sub_conv = executor._sub_agents["worker1"]
        assert sub_conv.state.security_analyzer == parent.state.security_analyzer

        obs = executor(
            DelegateAction(command="delegate", tasks={"worker1": "Run commands"}),
            parent,
        )
        analysis_events = [
            ev for ev in sub_conv.state.events if isinstance(ev, SecurityAnalysisEvent)
        ]
        assert len(analysis_events) == 2
        recorded_risks = [risk for ev in analysis_events for risk in ev.risks.values()]
        assert recorded_risks == [SecurityRisk.LOW, SecurityRisk.HIGH]

        rejections = [
            ev for ev in sub_conv.state.events if isinstance(ev, UserRejectObservation)
        ]
        assert len(rejections) == 1
        executor.close()

    parent.close()
    assert safe_file.exists()
    assert not canary.exists()
    assert not obs.is_error


@pytest.mark.parametrize("approve", [True, False])
def test_delegate_executor_explicit_confirmation_handler(
    tmp_path: Path, approve: bool
) -> None:
    """Explicit confirmation_handler on DelegateExecutor approves on True and
    rejects with 'User rejected the actions' on False.
    """
    canary = tmp_path / f"delegate_handler_{approve}.txt"
    seen: list[tuple[str, int]] = []

    def handler(agent_id: str, pending: list[Any]) -> bool:
        seen.append((agent_id, len(pending)))
        return approve

    llm = TestLLM.from_messages(
        [
            _cmd_msg("call_dh1", f"echo OK > {canary}"),
            _text_msg("Done."),
        ]
    )
    parent = LocalConversation(
        agent=Agent(llm=llm, tools=[Tool(name="terminal")]),
        workspace=str(tmp_path),
        visualizer=None,
    )
    parent.set_confirmation_policy(AlwaysConfirm())

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        executor = DelegateExecutor(confirmation_handler=handler)
        executor(
            DelegateAction(
                command="spawn", ids=["worker1"], agent_types=["bash-runner"]
            ),
            parent,
        )
        executor(
            DelegateAction(command="delegate", tasks={"worker1": "Run"}),
            parent,
        )
        sub_conv = executor._sub_agents["worker1"]
        rejections = [
            ev for ev in sub_conv.state.events if isinstance(ev, UserRejectObservation)
        ]
        if approve:
            assert rejections == []
        else:
            assert len(rejections) == 1
            assert rejections[0].rejection_reason == "User rejected the actions"
        executor.close()

    parent.close()
    assert seen == [("worker1", 1)]
    assert canary.exists() is approve


def test_delegate_executor_never_confirm_executes_normally(tmp_path: Path) -> None:
    """NeverConfirm sub-agents in DelegateExecutor complete normally."""
    marker = tmp_path / "delegate_normal.txt"
    llm = TestLLM.from_messages(
        [
            _cmd_msg("call_dn1", f"echo NORMAL > {marker}"),
            _text_msg("Done."),
        ]
    )
    parent = LocalConversation(
        agent=Agent(llm=llm, tools=[Tool(name="terminal")]),
        workspace=str(tmp_path),
        visualizer=None,
    )
    parent.set_confirmation_policy(NeverConfirm())

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        executor = DelegateExecutor()
        executor(
            DelegateAction(
                command="spawn", ids=["worker1"], agent_types=["bash-runner"]
            ),
            parent,
        )
        executor(
            DelegateAction(command="delegate", tasks={"worker1": "Run"}),
            parent,
        )
        executor.close()

    parent.close()
    assert marker.exists()
    assert marker.read_text().strip() == "NORMAL"
