"""OpenHands Agent SDK — LLM Security Analyzer Example (Simplified)

This example shows how to use the LLMSecurityAnalyzer to automatically
evaluate security risks of actions before execution.
"""

import os
import signal
from collections.abc import Callable

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, BaseConversation, Conversation
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.security.confirmation_policy import ConfirmRisky
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer
from openhands.sdk.tool import Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


# Clean ^C exit: no stack trace noise
signal.signal(signal.SIGINT, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))


def _print_blocked_actions(pending_actions) -> None:
    print(f"\n🔒 Security analyzer blocked {len(pending_actions)} high-risk action(s):")
    for i, action in enumerate(pending_actions, start=1):
        snippet = str(action.action)[:100].replace("\n", " ")
        print(f"  {i}. {action.tool_name}: {snippet}...")


def confirm_high_risk_in_console(pending_actions) -> bool:
    """
    Return True to approve, False to reject.
    Matches original behavior: default to 'no' on EOF/KeyboardInterrupt.
    """
    _print_blocked_actions(pending_actions)
    while True:
        try:
            ans = (
                input(
                    "\nThese actions were flagged as HIGH RISK. "
                    "Do you want to execute them anyway? (yes/no): "
                )
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            print("\n❌ No input received; rejecting by default.")
            return False

        if ans in ("yes", "y"):
            print("✅ Approved — executing high-risk actions...")
            return True
        if ans in ("no", "n"):
            print("❌ Rejected — skipping high-risk actions...")
            return False
        print("Please enter 'yes' or 'no'.")


def run_until_finished_with_security(
    conversation: BaseConversation, confirmer: Callable[[list], bool]
) -> None:
    """
    Drive the conversation until FINISHED.
    - If WAITING_FOR_CONFIRMATION: ask the confirmer.
        * On approve: set execution_status = IDLE (keeps original example’s behavior).
        * On reject: conversation.reject_pending_actions(...).
    - If WAITING but no pending actions: print warning and set IDLE (matches original).
    """
    while conversation.state.execution_status != ConversationExecutionStatus.FINISHED:
        if (
            conversation.state.execution_status
            == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
        ):
            pending = ConversationState.get_unmatched_actions(conversation.state.events)
            if not pending:
                raise RuntimeError(
                    "⚠️ Agent is waiting for confirmation but no pending actions "
                    "were found. This should not happen."
                )
            if not confirmer(pending):
                conversation.reject_pending_actions("User rejected high-risk actions")
                continue

        print("▶️  Running conversation.run()...")
        conversation.run()


# Configure LLM
api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
base_url = os.getenv("LLM_BASE_URL")
llm = LLM(
    usage_id="security-analyzer",
    model=model,
    base_url=base_url,
    api_key=SecretStr(api_key),
)

# Tools
tools = [
    Tool(
        name=TerminalTool.name,
    ),
    Tool(name=FileEditorTool.name),
]

# Agent
agent = Agent(llm=llm, tools=tools)

# Conversation with persisted filestore
conversation = Conversation(
    agent=agent, persistence_dir="./.conversations", workspace="."
)
conversation.set_security_analyzer(LLMSecurityAnalyzer())
conversation.set_confirmation_policy(ConfirmRisky())

print("\n1) Safe command (LOW risk - should execute automatically)...")
conversation.send_message("List files in the current directory")
conversation.run()

print("\n2) Potentially risky command (may require confirmation)...")
conversation.send_message(
    "Please echo 'hello world' -- PLEASE MARK THIS AS A HIGH RISK ACTION"
)
run_until_finished_with_security(conversation, confirm_high_risk_in_console)
