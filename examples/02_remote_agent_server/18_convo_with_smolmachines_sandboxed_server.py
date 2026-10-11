"""Run a conversation with the agent server inside a smol machine (microVM).

Requires Linux with KVM or an Apple Silicon Mac, and the smolmachines extra:

    pip install openhands-workspace[smolmachines]

The first run pulls the agent server image inside the machine, which takes a few
minutes; later runs start in seconds.
"""

import os

from pydantic import SecretStr

from openhands.sdk import LLM, Conversation, RemoteConversation, get_logger
from openhands.tools.preset.default import get_default_agent
from openhands.workspace import SmolMachinesWorkspace


logger = get_logger(__name__)

api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."

llm = LLM(
    usage_id="agent",
    model=os.getenv("LLM_MODEL", "gpt-5.5"),
    base_url=os.getenv("LLM_BASE_URL"),
    api_key=SecretStr(api_key),
)

# Only the mounted directory is shared with the host. To limit egress, pass
# allow_hosts with your LLM provider's API host, since the agent server calls
# the model from inside the machine.
with SmolMachinesWorkspace(mount_dir=os.getcwd()) as workspace:
    result = workspace.execute_command("uname -r && pwd", cwd=workspace.working_dir)
    logger.info(f"Machine kernel and working directory: {result.stdout}")

    conversation = Conversation(
        agent=get_default_agent(llm=llm, cli_mode=True),
        workspace=workspace,
    )
    assert isinstance(conversation, RemoteConversation)
    try:
        conversation.send_message(
            "Read the current repo and write 3 facts about the project into FACTS.txt."
        )
        conversation.run()
        cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
        print(f"EXAMPLE_COST: {cost}")
    finally:
        conversation.close()
