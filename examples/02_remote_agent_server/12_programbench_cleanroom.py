"""ProgramBench cleanroom example.

ProgramBench (https://programbench.com) ships per-task Docker images that
contain a single compiled binary plus its public-facing documentation. The
benchmark asks the agent to **rebuild a working codebase from scratch**
using only those artifacts — no internet access allowed.

This example shows how to drive a single ProgramBench task with the SDK by
layering ``openhands-agent-server`` on top of one of the upstream cleanroom
images (``programbench/<owner>_1776_<repo>.<sha>:task_cleanroom``).

Note: full ProgramBench evaluation (rendering 200 tasks, collecting
submission tarballs, and grading them with ``programbench eval``) lives in
the OpenHands ``benchmarks`` repo at
https://github.com/OpenHands/benchmarks/tree/main/benchmarks/programbench.

Run with::

    LLM_API_KEY=... \\
    PROGRAMBENCH_TASK_IMAGE=programbench/abishekvashok_1776_cmatrix.5c082c6:task_cleanroom \\
    python examples/02_remote_agent_server/12_programbench_cleanroom.py
"""  # noqa: E501

import os
import platform
import shlex
import time
from typing import Literal

from pydantic import SecretStr

from openhands.sdk import LLM, Conversation, RemoteConversation, get_logger
from openhands.tools.preset.default import get_default_agent
from openhands.workspace import DockerDevWorkspace


logger = get_logger(__name__)


def detect_platform() -> Literal["linux/amd64", "linux/arm64"]:
    """ProgramBench cleanroom images are linux/amd64 only.

    On Apple Silicon hosts this will run under emulation — slow but
    functional. We surface a clear log so the user knows what to expect.
    """
    machine = platform.machine().lower()
    if "arm" in machine or "aarch64" in machine:
        logger.warning(
            "ProgramBench task images are linux/amd64 only. Running under "
            "QEMU emulation on this host will be slow."
        )
    return "linux/amd64"


api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."

# Default to the canonical first task in the upstream list — picked because
# it's small (`cmatrix`) and fast to pull.
task_image = os.getenv(
    "PROGRAMBENCH_TASK_IMAGE",
    "programbench/abishekvashok_1776_cmatrix.5c082c6:task_cleanroom",
)
logger.info("Using ProgramBench task image: %s", task_image)

llm = LLM(
    usage_id="agent",
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    base_url=os.getenv("LLM_BASE_URL"),
    api_key=SecretStr(api_key),
)

# DockerDevWorkspace builds an agent-server layer on top of any base image
# on-the-fly, so we can drop the SDK into ProgramBench's cleanroom container
# without publishing a custom image. ``target="source-minimal"`` keeps the
# layered image small.
#
# After the build phase, the running container is started with no network
# (``network="none"``) — ProgramBench's leaderboard rules forbid the agent
# from accessing the internet. Disable this only for debugging.
with DockerDevWorkspace(
    base_image=task_image,
    working_dir="/workspace",
    target="source-minimal",
    platform=detect_platform(),
    network="none",
) as workspace:
    # Sanity-check that the cleanroom binary and docs are visible to us.
    listing = workspace.execute_command("ls -la /workspace")
    logger.info("Cleanroom /workspace listing:\n%s", listing.stdout)

    agent = get_default_agent(llm=llm, cli_mode=True)

    received_events: list = []
    last_event_time = {"ts": time.time()}

    def event_callback(event) -> None:
        last_event_time["ts"] = time.time()
        received_events.append(event)

    conversation = Conversation(
        agent=agent,
        workspace=workspace,
        callbacks=[event_callback],
    )
    assert isinstance(conversation, RemoteConversation)

    try:
        # Trim instruction — the benchmarks repo's prompt is more elaborate.
        # This example just demonstrates wiring; for a leaderboard-shaped run
        # use ``programbench-infer`` from OpenHands/benchmarks.
        conversation.send_message(
            "You are inside a ProgramBench cleanroom container. "
            "/workspace contains a single compiled program plus its docs. "
            "You have no internet access. Find the binary, run it with "
            "--help and a couple of representative inputs, summarise its "
            "behaviour, and sketch a plan for rebuilding it from scratch. "
            "Do not modify or move the original binary."
        )
        conversation.run()

        # Show a small sample of the agent's exploration so the example
        # output is meaningful even without running an eval.
        sample = workspace.execute_command(
            "ls -la /workspace && echo '---' && "
            f"find /workspace -maxdepth 3 -newer /etc/hostname "
            f"-not -path {shlex.quote('*/proc/*')} 2>/dev/null | head -30"
        )
        logger.info("Files the agent touched:\n%s", sample.stdout)

        cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
        print(f"EXAMPLE_COST: {cost}")
    finally:
        print("\n🧹 Cleaning up conversation...")
        conversation.close()
