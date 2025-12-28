"""Example: Using custom tools with remote agent server.

This example demonstrates how to use custom tools with a remote agent server
by building a custom base image that includes the tool implementation.

Prerequisites:
    1. Build the custom base image first:
       cd examples/02_remote_agent_server/05_custom_tool
       ./build_custom_image.sh

    2. Set LLM_API_KEY environment variable

The workflow is:
1. Define a custom tool (LogDataTool for logging structured data to JSON)
2. Create a simple Dockerfile that copies the tool into the base image
3. Build the custom base image
4. Use DockerDevWorkspace with base_image pointing to the custom image
5. DockerDevWorkspace builds the agent server on top of the custom base image
6. The server dynamically registers tools when the client creates a conversation
7. The agent can use the custom tool during execution
8. Verify the logged data by reading the JSON file from the workspace

This pattern is useful for:
- Collecting structured data during agent runs (logs, metrics, events)
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
from openhands.workspace import DockerDevWorkspace


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

# Custom base image tag (contains custom tools, agent server built on top)
CUSTOM_BASE_IMAGE_TAG = "custom-base-image:latest"

# 2) Check if custom base image exists, build if not
logger.info(f"🔍 Checking for custom base image: {CUSTOM_BASE_IMAGE_TAG}")
result = subprocess.run(
    ["docker", "images", "-q", CUSTOM_BASE_IMAGE_TAG],
    capture_output=True,
    text=True,
    check=False,
)

if not result.stdout.strip():
    logger.info("⚠️  Custom base image not found. Building...")
    logger.info("📦 Building custom base image with custom tools...")
    build_script = example_dir / "build_custom_image.sh"
    try:
        subprocess.run(
            [str(build_script), CUSTOM_BASE_IMAGE_TAG],
            cwd=str(example_dir),
            check=True,
        )
        logger.info("✅ Custom base image built successfully!")
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Failed to build custom base image: {e}")
        logger.error("Please run ./build_custom_image.sh manually and fix any errors.")
        sys.exit(1)
else:
    logger.info(f"✅ Custom base image found: {CUSTOM_BASE_IMAGE_TAG}")

# 3) Create a DockerDevWorkspace with the custom base image
#    DockerDevWorkspace will build the agent server on top of this base image
logger.info("🚀 Building and starting agent server with custom tools...")
logger.info("📦 This may take a few minutes on first run...")

with DockerDevWorkspace(
    base_image=CUSTOM_BASE_IMAGE_TAG,
    host_port=8011,
    platform=detect_platform(),
) as workspace:
    logger.info("✅ Custom agent server started!")

    # 4) Import custom tools to register them in the client's registry
    #    This allows the client to send the module qualname to the server
    #    The server will then import the same module and execute the tool
    import custom_tools.log_data  # noqa: F401

    # 5) Create agent with custom tools
    #    Note: We specify the tool here, but it's actually executed on the server
    #    Get default tools and add our custom tool
    from openhands.sdk import Agent
    from openhands.tools.preset.default import get_default_condenser, get_default_tools

    tools = get_default_tools(enable_browser=False)
    # Add our custom tool!
    tools.append(Tool(name="LogDataTool"))

    agent = Agent(
        llm=llm,
        tools=tools,
        system_prompt_kwargs={"cli_mode": True},
        condenser=get_default_condenser(
            llm=llm.model_copy(update={"usage_id": "condenser"})
        ),
    )

    # 6) Set up callback collection
    received_events: list = []
    last_event_time = {"ts": time.time()}

    def event_callback(event) -> None:
        event_type = type(event).__name__
        logger.info(f"🔔 Callback received event: {event_type}\n{event}")
        received_events.append(event)
        last_event_time["ts"] = time.time()

    # 7) Test the workspace with a simple command
    result = workspace.execute_command(
        "echo 'Custom agent server ready!' && python --version"
    )
    logger.info(
        f"Command '{result.command}' completed with exit code {result.exit_code}"
    )
    logger.info(f"Output: {result.stdout}")

    # 8) Create conversation with the custom agent
    conversation = Conversation(
        agent=agent,
        workspace=workspace,
        callbacks=[event_callback],
    )
    assert isinstance(conversation, RemoteConversation)

    try:
        logger.info(f"\n📋 Conversation ID: {conversation.state.id}")

        logger.info("📝 Sending task to analyze files and log findings...")
        conversation.send_message(
            "Please analyze the Python files in the current directory. "
            "Use the LogDataTool to log your findings as you work. "
            "For example:\n"
            "- Log when you start analyzing a file (level: info)\n"
            "- Log any interesting patterns you find (level: info)\n"
            "- Log any potential issues (level: warning)\n"
            "- Include relevant data like file names, line numbers, etc.\n\n"
            "Make at least 3 log entries using the LogDataTool."
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

        # 9) Read the logged data from the JSON file
        logger.info("\n📊 Logged Data Summary:")
        logger.info("=" * 80)

        # Read the log file from the workspace
        log_result = workspace.execute_command("cat /tmp/agent_data.json 2>/dev/null")
        if log_result.exit_code == 0 and log_result.stdout.strip():
            import json

            try:
                log_entries = json.loads(log_result.stdout)
                logger.info(f"Found {len(log_entries)} log entries:\n")
                for i, entry in enumerate(log_entries, 1):
                    logger.info(f"Entry {i}:")
                    logger.info(f"  Timestamp: {entry.get('timestamp', 'N/A')}")
                    logger.info(f"  Level: {entry.get('level', 'N/A')}")
                    logger.info(f"  Message: {entry.get('message', 'N/A')}")
                    if entry.get("data"):
                        logger.info(f"  Data: {json.dumps(entry['data'], indent=4)}")
                    logger.info("")
            except json.JSONDecodeError:
                logger.info("Log file exists but couldn't parse JSON")
                logger.info(f"Raw content: {log_result.stdout}")
        else:
            logger.info("No log file found (agent may not have used the tool)")

        logger.info("=" * 80)

        cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
        print(f"\nEXAMPLE_COST: {cost}")

    finally:
        logger.info("\n🧹 Cleaning up conversation...")
        conversation.close()

logger.info("\n✅ Example completed successfully!")
logger.info("\nThis example demonstrated how to:")
logger.info("1. Create a custom tool that logs structured data to JSON")
logger.info("2. Build a simple base image with the custom tool")
logger.info("3. Use DockerDevWorkspace with base_image to build agent server on top")
logger.info("4. Enable dynamic tool registration on the server")
logger.info("5. Use the custom tool during agent execution")
logger.info("6. Read the logged data back from the workspace")
