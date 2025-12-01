"""OpenHands Agent SDK — Confirmation Mode Example"""

import os
import signal
from collections.abc import Callable

from pydantic import SecretStr

from openhands.sdk import LLM, BaseConversation, Conversation
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.security.confirmation_policy import AlwaysConfirm, NeverConfirm
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer
from openhands.tools.preset.default import get_default_agent


# Make ^C a clean exit instead of a stack trace
signal.signal(signal.SIGINT, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))


def _print_action_preview(pending_actions) -> None:
    print(f"\n🔍 Agent created {len(pending_actions)} action(s) awaiting confirmation:")
    for i, action in enumerate(pending_actions, start=1):
        snippet = str(action.action)[:100].replace("\n", " ")
        print(f"  {i}. {action.tool_name}: {snippet}...")


def confirm_in_console(pending_actions) -> bool:
    """
    Return True to approve, False to reject.
    Default to 'no' on EOF/KeyboardInterrupt (matches original behavior).
    """
    _print_action_preview(pending_actions)
    while True:
        try:
            ans = (
                input("\nDo you want to execute these actions? (yes/no): ")
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            print("\n❌ No input received; rejecting by default.")
            return False

        if ans in ("yes", "y"):
            print("✅ Approved — executing actions…")
            return True
        if ans in ("no", "n"):
            print("❌ Rejected — skipping actions…")
            return False
        print("Please enter 'yes' or 'no'.")


def run_until_finished(conversation: BaseConversation, confirmer: Callable) -> None:
    """
    Drive the conversation until FINISHED.
    If WAITING_FOR_CONFIRMATION, ask the confirmer;
    on reject, call reject_pending_actions().
    Preserves original error if agent waits but no actions exist.
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
                conversation.reject_pending_actions("User rejected the actions")
                # Let the agent produce a new step or finish
                continue

        print("▶️  Running conversation.run()…")
        conversation.run()


# Configure LLM
api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
base_url = os.getenv("LLM_BASE_URL")
llm = LLM(
    usage_id="agent",
    model=model,
    base_url=base_url,
    api_key=SecretStr(api_key),
)

agent = get_default_agent(llm=llm)
conversation = Conversation(agent=agent, workspace=os.getcwd())

# Conditionally add security analyzer based on environment variable
add_security_analyzer = bool(os.getenv("ADD_SECURITY_ANALYZER", "").strip())
if add_security_analyzer:
    print("Agent security analyzer added.")
    conversation.set_security_analyzer(LLMSecurityAnalyzer())

# 1) Confirmation mode ON
conversation.set_confirmation_policy(AlwaysConfirm())
print("\n1) Command that will likely create actions…")
conversation.send_message("Please list the files in the current directory using ls -la")
run_until_finished(conversation, confirm_in_console)

# 2) A command the user may choose to reject
print("\n2) Command the user may choose to reject…")
conversation.send_message("Please create a file called 'dangerous_file.txt'")
run_until_finished(conversation, confirm_in_console)

# 3) Simple greeting (no actions expected)
print("\n3) Simple greeting (no actions expected)…")
conversation.send_message("Just say hello to me")
run_until_finished(conversation, confirm_in_console)

# 4) Disable confirmation mode and run commands directly
print("\n4) Disable confirmation mode and run a command…")
conversation.set_confirmation_policy(NeverConfirm())
conversation.send_message("Please echo 'Hello from confirmation mode example!'")
conversation.run()

conversation.send_message(
    "Please delete any file that was created during this conversation."
)
conversation.run()

print("\n=== Example Complete ===")
print("Key points:")
print(
    "- conversation.run() creates actions; confirmation mode "
    "sets execution_status=WAITING_FOR_CONFIRMATION"
)
print("- User confirmation is handled via a single reusable function")
print("- Rejection uses conversation.reject_pending_actions() and the loop continues")
print("- Simple responses work normally without actions")
print("- Confirmation policy is toggled with conversation.set_confirmation_policy()")
