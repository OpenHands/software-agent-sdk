"""OpenHands Agent SDK — Configurable Security Policy Example

This example demonstrates how to use a custom security policy template
with an agent. Security policies define risk assessment guidelines that
help agents evaluate the safety of their actions.

By default, agents use the built-in security_policy.j2 template. This
example shows how to:
1. Use the default security policy
2. Provide a custom security policy template embedded in the script
3. Apply the custom policy to guide agent behavior
"""

import os
import tempfile
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Agent,
    Conversation,
    Event,
    LLMConvertibleEvent,
    get_logger,
)
from openhands.sdk.tool import Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


logger = get_logger(__name__)

# Define a custom security policy template inline
CUSTOM_SECURITY_POLICY = (
    "# 🔐 Custom Security Risk Policy\n"
    "When using tools that support the security_risk parameter, assess the "
    "safety risk of your actions:\n"
    "\n"
    "- **LOW**: Safe read-only actions.\n"
    "  - Viewing files, calculations, documentation.\n"
    "- **MEDIUM**: Moderate container-scoped actions.\n"
    "  - File modifications, package installations.\n"
    "- **HIGH**: Potentially dangerous actions.\n"
    "  - Network access, system modifications, data exfiltration.\n"
    "\n"
    "**Custom Rules**\n"
    "- Always prioritize user data safety.\n"
    "- Escalate to **HIGH** for any external data transmission.\n"
)

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

# Tools
cwd = os.getcwd()
tools = [
    Tool(name=TerminalTool.name),
    Tool(name=FileEditorTool.name),
]

# Example 1: Agent with default security policy
print("=" * 100)
print("Example 1: Agent with default security policy")
print("=" * 100)
default_agent = Agent(llm=llm, tools=tools)
print(f"Security policy filename: {default_agent.security_policy_filename}")
print("\nDefault security policy is embedded in the agent's system message.")

# Example 2: Agent with custom security policy
print("\n" + "=" * 100)
print("Example 2: Agent with custom security policy")
print("=" * 100)

# Create a temporary file for the custom security policy
with tempfile.NamedTemporaryFile(
    mode="w", suffix=".j2", delete=False, encoding="utf-8"
) as temp_file:
    temp_file.write(CUSTOM_SECURITY_POLICY)
    custom_policy_path = temp_file.name

try:
    # Create agent with custom security policy (using absolute path)
    custom_agent = Agent(
        llm=llm,
        tools=tools,
        security_policy_filename=custom_policy_path,
    )
    print(f"Security policy filename: {custom_agent.security_policy_filename}")
    print("\nCustom security policy loaded from temporary file.")

    # Verify the custom policy is in the system message
    system_message = custom_agent.static_system_message
    if "Custom Security Risk Policy" in system_message:
        print("✓ Custom security policy successfully embedded in system message.")
    else:
        print("✗ Custom security policy not found in system message.")

    # Run a conversation with the custom agent
    print("\n" + "=" * 100)
    print("Running conversation with custom security policy")
    print("=" * 100)

    llm_messages = []  # collect raw LLM messages

    def conversation_callback(event: Event):
        if isinstance(event, LLMConvertibleEvent):
            llm_messages.append(event.to_llm_message())

    conversation = Conversation(
        agent=custom_agent,
        callbacks=[conversation_callback],
        workspace=".",
    )

    conversation.send_message(
        "Please create a simple Python script named hello.py that prints "
        "'Hello, World!'. Make sure to follow security best practices."
    )
    conversation.run()

    print("\n" + "=" * 100)
    print("Conversation finished.")
    print(f"Total LLM messages: {len(llm_messages)}")
    print("=" * 100)

    # Report cost
    cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
    print(f"EXAMPLE_COST: {cost}")

finally:
    # Clean up temporary file
    Path(custom_policy_path).unlink(missing_ok=True)

print("\n" + "=" * 100)
print("Example Summary")
print("=" * 100)
print("This example demonstrated:")
print("1. Using the default security policy (security_policy.j2)")
print("2. Creating a custom security policy template")
print("3. Applying the custom policy via security_policy_filename parameter")
print("4. Running a conversation with the custom security policy")
print(
    "\nYou can customize security policies to match your organization's "
    "specific requirements."
)
