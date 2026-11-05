import os
import platform
import tempfile
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Conversation, RemoteConversation, get_logger
from openhands.tools.preset.default import get_default_agent
from openhands.workspace import PodmanWorkspace


logger = get_logger(__name__)

# 1) Ensure we have LLM API key
api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."

llm = LLM(
    usage_id="agent",
    model=os.getenv("LLM_MODEL", "gpt-5.5"),
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
    # SDK_SHA is the canonical commit SHA set by CI workflows (avoids the
    # built-in GITHUB_SHA which resolves to the merge-commit on PRs).
    sha = os.getenv("SDK_SHA") or os.getenv("GITHUB_SHA")
    if sha:
        return f"ghcr.io/openhands/agent-server:{sha[:7]}-python-{arch}"
    return "ghcr.io/openhands/agent-server:latest-python"


# 2) Create a project directory on the host. It is the only host path the
#    container can see. The agent server keeps its own state under
#    /workspace, so the project is mounted one level below it.
project_dir = Path(tempfile.mkdtemp(prefix="podman-project-"))
(project_dir / "README.md").write_text("# Demo project\n")

# 3) Start the agent server in a rootless Podman container. No daemon or root
#    is needed. The default `userns` maps the host user to the container's
#    `openhands` user, so files the agent writes belong to the host user.
server_image = get_server_image()
logger.info(f"Using server image: {server_image}")
with PodmanWorkspace(
    server_image=server_image,
    platform=detect_platform(),
    volumes=[f"{project_dir}:/workspace/project"],
    working_dir="/workspace/project",
) as workspace:
    result = workspace.execute_command("id && ls -la")
    logger.info(f"Command exited with {result.exit_code}:\n{result.stdout}")

    # 4) Run a conversation that writes into the mounted project
    agent = get_default_agent(llm=llm, cli_mode=True)
    conversation = Conversation(agent=agent, workspace=workspace)
    assert isinstance(conversation, RemoteConversation)
    try:
        conversation.send_message(
            "Write three facts about this project into FACTS.txt in the "
            "current directory."
        )
        conversation.run()

        cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
        print(f"EXAMPLE_COST: {cost}")
    finally:
        conversation.close()

# 5) Files the agent wrote are owned by the host user
facts = project_dir / "FACTS.txt"
print(f"{facts}: uid {facts.stat().st_uid} (host uid {os.getuid()})")
for path in project_dir.rglob("*"):
    assert path.stat().st_uid == os.getuid(), f"{path} is not owned by the host user"
