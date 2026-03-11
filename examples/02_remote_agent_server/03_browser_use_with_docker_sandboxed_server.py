import os
import platform
import time

from pydantic import SecretStr

from openhands.sdk import LLM, Conversation, get_logger
from openhands.sdk.conversation.impl.remote_conversation import RemoteConversation
from openhands.tools.preset.default import get_default_agent
from openhands.workspace import DockerWorkspace


logger = get_logger(__name__)

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


# Create a Docker-based remote workspace with extra ports for browser access.
# Use `DockerWorkspace` with a pre-built image or `DockerDevWorkspace` to
# automatically build the image on-demand.
#    with DockerDevWorkspace(
#        # dynamically build agent-server image
#        base_image="nikolaik/python-nodejs:python3.13-nodejs22",
#        host_port=8010,
#        platform=detect_platform(),
#    ) as workspace:
server_image = get_server_image()
logger.info(f"Using server image: {server_image}")
with DockerWorkspace(
    server_image=server_image,
    host_port=8011,
    platform=detect_platform(),
    extra_ports=True,  # Expose extra ports for VSCode and VNC
) as workspace:
    """Extra ports allows you to check localhost:8012 for VNC"""

    # Create agent with browser tools enabled
    agent = get_default_agent(
        llm=llm,
        cli_mode=False,  # CLI mode = False will enable browser tools
    )

    # Set up callback collection
    received_events: list = []
    last_event_time = {"ts": time.time()}

    def event_callback(event) -> None:
        event_type = type(event).__name__
        logger.info(f"🔔 Callback received event: {event_type}\n{event}")
        received_events.append(event)
        last_event_time["ts"] = time.time()

    # Create RemoteConversation using the workspace
    conversation = Conversation(
        agent=agent,
        workspace=workspace,
        callbacks=[event_callback],
    )
    assert isinstance(conversation, RemoteConversation)

    logger.info(f"\n📋 Conversation ID: {conversation.state.id}")
    logger.info("📝 Sending first message...")
    conversation.send_message(
        "Could you go to https://openhands.dev/ blog page and summarize main "
        "points of the latest blog?"
    )
    conversation.run()

    cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
    print(f"EXAMPLE_COST: {cost}")

    if os.getenv("CI"):
        logger.info(
            "CI environment detected; skipping interactive prompt and closing workspace."  # noqa: E501
        )
    else:
        # Wait for user confirm to exit when running locally
        y = None
        while y != "y":
            y = input(
                "Because you've enabled extra_ports=True in DockerDevWorkspace, "
                "you can open a browser tab to see the *actual* browser OpenHands "
                "is interacting with via VNC.\n\n"
                "Link: http://localhost:8012/vnc.html?autoconnect=1&resize=remote\n\n"
                "Press 'y' and Enter to exit and terminate the workspace.\n"
                ">> "
            )
