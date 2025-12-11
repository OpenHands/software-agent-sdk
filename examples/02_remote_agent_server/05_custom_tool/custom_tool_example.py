"""Example: Using custom tools with remote agent server.

This example demonstrates how to use custom tools with a remote agent server
by building a custom Docker image that includes the tool implementation.

Prerequisites:
    1. Build the custom image first:
       cd examples/02_remote_agent_server/05_custom_tool
       ./build_custom_image.sh

    2. Set LLM_API_KEY environment variable

The workflow is:
1. Define a custom tool (in this case, ReportBugTool for structured bug reporting)
2. Create a Dockerfile that includes the tool in the agent server image
3. Build the custom image (extends base agent server image)
4. Use DockerWorkspace with the custom image
5. The server dynamically registers tools when the client creates a conversation
6. The agent can use the custom tool during execution
7. The client can access structured data collected by the tool

This pattern is useful for:
- Collecting structured data during agent runs (bugs, metrics, etc.)
- Implementing custom integrations with external systems
- Adding domain-specific operations to the agent
"""

import os
import platform
import subprocess
import sys
import time
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Conversation,
    RemoteConversation,
    Tool,
    get_logger,
)
from openhands.workspace import DockerWorkspace


logger = get_logger(__name__)

# 1) Ensure we have LLM API key
api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."

llm = LLM(
    usage_id="agent",
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    base_url=os.getenv("LLM_BASE_URL"),
    api_key=SecretStr(api_key),
)


def detect_platform():
    """Detects the correct Docker platform string."""
    machine = platform.machine().lower()
    if "arm" in machine or "aarch64" in machine:
        return "linux/arm64"
    return "linux/amd64"


# Get the directory containing this script
example_dir = Path(__file__).parent.absolute()

# Custom image tag
CUSTOM_IMAGE_TAG = "custom-agent-server:latest"

# 2) Check if custom image exists, build if not
logger.info(f"🔍 Checking for custom image: {CUSTOM_IMAGE_TAG}")
result = subprocess.run(
    ["docker", "images", "-q", CUSTOM_IMAGE_TAG],
    capture_output=True,
    text=True,
    check=False,
)

if not result.stdout.strip():
    logger.info("⚠️  Custom image not found. Building...")
    logger.info("📦 This may take a few minutes on first run...")
    build_script = example_dir / "build_custom_image.sh"
    try:
        subprocess.run(
            [str(build_script), CUSTOM_IMAGE_TAG],
            cwd=str(example_dir),
            check=True,
        )
        logger.info("✅ Custom image built successfully!")
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Failed to build custom image: {e}")
        logger.error("Please run ./build_custom_image.sh manually and fix any errors.")
        sys.exit(1)
else:
    logger.info(f"✅ Custom image found: {CUSTOM_IMAGE_TAG}")

# 3) Create a DockerWorkspace with the custom agent server image
logger.info("🚀 Starting custom agent server...")

with DockerWorkspace(
    server_image=CUSTOM_IMAGE_TAG,
    host_port=8011,
    platform=detect_platform(),
) as workspace:
    logger.info("✅ Custom agent server started!")

    # 4) Create agent with custom tools
    #    Note: We specify the tool here, but it's actually executed on the server
    #    Get default tools and add our custom tool
    from openhands.sdk import Agent
    from openhands.tools.preset.default import get_default_condenser, get_default_tools

    tools = get_default_tools(enable_browser=False)
    # Add our custom tool!
    tools.append(Tool(name="ReportBugTool"))

    agent = Agent(
        llm=llm,
        tools=tools,
        system_prompt_kwargs={"cli_mode": True},
        condenser=get_default_condenser(
            llm=llm.model_copy(update={"usage_id": "condenser"})
        ),
    )

    # 5) Set up callback collection
    received_events: list = []
    last_event_time = {"ts": time.time()}

    def event_callback(event) -> None:
        event_type = type(event).__name__
        logger.info(f"🔔 Callback received event: {event_type}\n{event}")
        received_events.append(event)
        last_event_time["ts"] = time.time()

    # 6) Test the workspace with a simple command
    result = workspace.execute_command(
        "echo 'Custom agent server ready!' && python --version"
    )
    logger.info(
        f"Command '{result.command}' completed with exit code {result.exit_code}"
    )
    logger.info(f"Output: {result.stdout}")

    # 7) Create conversation with the custom agent
    conversation = Conversation(
        agent=agent,
        workspace=workspace,
        callbacks=[event_callback],
    )
    assert isinstance(conversation, RemoteConversation)

    try:
        logger.info(f"\n📋 Conversation ID: {conversation.state.id}")

        logger.info("📝 Sending task to find and report bugs...")
        conversation.send_message(
            "Please analyze the Python files in the current directory "
            "and use the ReportBugTool to report any potential bugs you find. "
            "For each bug, include:\n"
            "- A clear title\n"
            "- Detailed description\n"
            "- Severity level (low, medium, high, critical)\n"
            "- Steps to reproduce if applicable\n"
            "- Affected files\n\n"
            "Look for issues like:\n"
            "- Missing error handling\n"
            "- Potential null/None reference errors\n"
            "- Type inconsistencies\n"
            "- Resource leaks\n"
            "Report at least 2 bugs using the ReportBugTool."
        )
        logger.info("🚀 Running conversation...")
        conversation.run()
        logger.info("✅ Task completed!")
        logger.info(f"Agent status: {conversation.state.execution_status}")

        # Wait for events to settle (no events for 2 seconds)
        logger.info("⏳ Waiting for events to stop...")
        while time.time() - last_event_time["ts"] < 2.0:
            time.sleep(0.1)
        logger.info("✅ Events have stopped")

        # 8) Access the collected bug reports
        # In a real application, you would:
        # - Query the agent server for collected data
        # - Store the data in a database
        # - Create Jira tickets
        # - Generate a bug report
        # For this example, we just show that the tool was used
        logger.info("\n📊 Bug Report Summary:")
        logger.info("=" * 80)
        logger.info(
            "In a production setup, you would access the collected bug reports here."
        )
        logger.info(
            "The ReportBugTool's executor stores all bugs with structured data."
        )
        logger.info(
            "You can query this data for downstream processing (Jira, reports, etc.)"
        )
        logger.info("=" * 80)

        # Report cost (must be before conversation.close())
        conversation.state._cached_state = (
            None  # Invalidate cache to fetch latest stats
        )
        cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
        print(f"\nEXAMPLE_COST: {cost}")

    finally:
        logger.info("\n🧹 Cleaning up conversation...")
        conversation.close()

logger.info("\n✅ Example completed successfully!")
logger.info("\nThis example demonstrated how to:")
logger.info("1. Create a custom tool for structured data collection")
logger.info("2. Build a custom agent server image with the tool")
logger.info("3. Use DockerDevWorkspace for automatic image building")
logger.info("4. Enable dynamic tool registration on the server")
logger.info("5. Use the custom tool during agent execution")
