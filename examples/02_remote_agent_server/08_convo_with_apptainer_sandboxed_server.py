import os
import platform
import time

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Conversation,
    RemoteConversation,
    get_logger,
)
from openhands.tools.preset.default import get_default_agent
from openhands.workspace import ApptainerWorkspace


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
    """Detects the correct platform string."""
    machine = platform.machine().lower()
    if "arm" in machine or "aarch64" in machine:
        return "linux/arm64"
    return "linux/amd64"


def get_server_image():
    """Get the server image tag, using PR-specific image in CI."""
    platform_str = detect_platform()
    arch = "arm64" if "arm64" in platform_str else "amd64"
    # If GITHUB_SHA is set (e.g. running in CI of a PR), use that to ensure consistency
    # Otherwise, use the latest image from main
    github_sha = os.getenv("GITHUB_SHA")
    if github_sha:
        return f"ghcr.io/openhands/agent-server:{github_sha[:7]}-python-{arch}"
    return "ghcr.io/openhands/agent-server:latest-python"


# 2) Create an Apptainer-based remote workspace that will set up and manage
#    the Apptainer container automatically. Use `ApptainerWorkspace` with a
#    pre-built agent server image.
#    Apptainer (formerly Singularity) doesn't require root access, making it
#    ideal for HPC and shared computing environments.
server_image = get_server_image()
logger.info(f"Using server image: {server_image}")
with ApptainerWorkspace(
    # use pre-built image for faster startup
    server_image=server_image,
    host_port=8010,
    platform=detect_platform(),
) as workspace:
    # 3) Create agent
    agent = get_default_agent(
        llm=llm,
        cli_mode=True,
    )

    # 4) Set up callback collection
    received_events: list = []
    last_event_time = {"ts": time.time()}

    def event_callback(event) -> None:
        event_type = type(event).__name__
        logger.info(f"🔔 Callback received event: {event_type}\n{event}")
        received_events.append(event)
        last_event_time["ts"] = time.time()

    # 5) Test the workspace with a simple command
    result = workspace.execute_command(
        "echo 'Hello from sandboxed environment!' && pwd"
    )
    logger.info(
        f"Command '{result.command}' completed with exit code {result.exit_code}"
    )
    logger.info(f"Output: {result.stdout}")
    conversation = Conversation(
        agent=agent,
        workspace=workspace,
        callbacks=[event_callback],
    )
    assert isinstance(conversation, RemoteConversation)

    try:
        logger.info(f"\n📋 Conversation ID: {conversation.state.id}")

        logger.info("📝 Sending first message...")
        conversation.send_message(
            "Read the current repo and write 3 facts about the project into FACTS.txt."
        )
        logger.info("🚀 Running conversation...")
        conversation.run()
        logger.info("✅ First task completed!")
        logger.info(f"Agent status: {conversation.state.execution_status}")

        # Wait for events to settle (no events for 2 seconds)
        logger.info("⏳ Waiting for events to stop...")
        while time.time() - last_event_time["ts"] < 2.0:
            time.sleep(0.1)
        logger.info("✅ Events have stopped")

        logger.info("🚀 Running conversation again...")
        conversation.send_message("Great! Now delete that file.")
        conversation.run()
        logger.info("✅ Second task completed!")

        # Report cost (must be before conversation.close())
        cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
        print(f"EXAMPLE_COST: {cost}")
    finally:
        print("\n🧹 Cleaning up conversation...")
        conversation.close()
