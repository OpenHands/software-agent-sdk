"""OpenHands Agent SDK — HOL Guard Security Analyzer Example.

This example adapts HOL Guard's side-effect-free shell command inspection to
OpenHands' SecurityAnalyzerBase. Install HOL Guard separately before running:

    pipx install --pip-args='--pre' hol-guard

Project: https://github.com/hashgraph-online/hol-guard

`hol-guard command test` classifies a shell command without executing it. This
adapter fails closed: only commands HOL Guard marks explicitly benign are LOW
risk; review, block, unknown, unsupported actions, or analyzer errors are HIGH
risk and flow through OpenHands' normal ConfirmRisky confirmation path.
"""

import json
import os
import subprocess
from collections.abc import Callable

from pydantic import Field, SecretStr

from openhands.sdk import LLM, Agent, BaseConversation, Conversation
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event import ActionEvent
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.confirmation_policy import ConfirmRisky
from openhands.sdk.security.risk import SecurityRisk
from openhands.sdk.tool import Tool
from openhands.tools.terminal import TerminalAction, TerminalTool


class HolGuardSecurityAnalyzer(SecurityAnalyzerBase):
    """Classify OpenHands terminal actions with the local HOL Guard CLI."""

    guard_executable: str = "hol-guard"
    workspace: str = "."
    timeout_seconds: float = Field(default=10.0, gt=0.0)

    def security_risk(self, action: ActionEvent) -> SecurityRisk:
        terminal_action = action.action
        if not isinstance(terminal_action, TerminalAction):
            return SecurityRisk.HIGH
        if terminal_action.is_input or not terminal_action.command.strip():
            return SecurityRisk.HIGH

        try:
            result = subprocess.run(
                [
                    self.guard_executable,
                    "command",
                    "test",
                    terminal_action.command,
                    "--json",
                ],
                cwd=self.workspace,
                capture_output=True,
                check=False,
                text=True,
                timeout=self.timeout_seconds,
            )
            if result.returncode != 0:
                return SecurityRisk.HIGH
            payload = json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            return SecurityRisk.HIGH

        if not isinstance(payload, dict):
            return SecurityRisk.HIGH

        minimum_action = str(payload.get("minimum_action") or "").lower()
        if minimum_action in {
            "review",
            "block",
            "require-reapproval",
            "sandbox-required",
        }:
            return SecurityRisk.HIGH

        classification = payload.get("classification")
        if (
            isinstance(classification, dict)
            and classification.get("explicitly_benign") is True
        ):
            return SecurityRisk.LOW

        return SecurityRisk.HIGH


def _print_blocked_actions(pending_actions) -> None:
    print(f"\nHOL Guard flagged {len(pending_actions)} action(s) for confirmation:")
    for i, action in enumerate(pending_actions, start=1):
        headline = action.summary or "(no summary provided)"
        snippet = str(action.action)[:100].replace("\n", " ")
        print(f"  {i}. [{action.tool_name}] {headline}")
        print(f"     {snippet}...")


def confirm_high_risk_in_console(pending_actions) -> bool:
    """Approve or reject actions that HOL Guard did not classify as benign."""
    _print_blocked_actions(pending_actions)
    while True:
        try:
            answer = (
                input("\nExecute these flagged actions anyway? (yes/no): ")
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            print("\nNo input received; rejecting by default.")
            return False

        if answer in ("yes", "y"):
            return True
        if answer in ("no", "n"):
            return False
        print("Please enter 'yes' or 'no'.")


def run_until_finished_with_security(
    conversation: BaseConversation, confirmer: Callable[[list], bool]
) -> None:
    """Run until completion, rejecting flagged pending actions by default."""
    while conversation.state.execution_status != ConversationExecutionStatus.FINISHED:
        if (
            conversation.state.execution_status
            == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
        ):
            pending = ConversationState.get_unmatched_actions(conversation.state.events)
            if not pending:
                raise RuntimeError(
                    "Agent is waiting for confirmation but no pending actions "
                    "were found."
                )
            if not confirmer(pending):
                conversation.reject_pending_actions(
                    "User rejected HOL Guard flagged actions"
                )
                continue
        conversation.run()


def main() -> None:
    api_key = os.getenv("LLM_API_KEY")
    assert api_key is not None, "LLM_API_KEY environment variable is not set."
    model = os.getenv("LLM_MODEL", "gpt-5.5")
    base_url = os.getenv("LLM_BASE_URL")
    llm = LLM(
        usage_id="hol-guard-security-analyzer",
        model=model,
        base_url=base_url,
        api_key=SecretStr(api_key),
    )

    agent = Agent(llm=llm, tools=[Tool(name=TerminalTool.name)])
    conversation = Conversation(
        agent=agent, persistence_dir="./.conversations", workspace="."
    )
    conversation.set_security_analyzer(HolGuardSecurityAnalyzer(workspace="."))
    conversation.set_confirmation_policy(ConfirmRisky())

    print("\n1) Safe command: HOL Guard should classify this as benign.")
    conversation.send_message("Use the terminal to run exactly: pwd")
    run_until_finished_with_security(conversation, confirm_high_risk_in_console)

    print("\n2) Risky command: HOL Guard should require confirmation.")
    conversation.send_message(
        "Use the terminal to run exactly: rm -rf /tmp/openhands-hol-guard-demo"
    )
    run_until_finished_with_security(conversation, confirm_high_risk_in_console)


if __name__ == "__main__":
    main()
