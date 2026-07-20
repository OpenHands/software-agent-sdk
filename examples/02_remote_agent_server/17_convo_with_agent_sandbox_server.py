"""Run a conversation whose agent server lives in a kubernetes-sigs/agent-sandbox pod.

Prerequisites (see agent_sandbox_deploy/README.md for a full kind walkthrough):
  1. A Kubernetes cluster (kind / minikube / GKE) with the agent-sandbox controller
     and extensions installed, reachable via your kubeconfig.
  2. The agent-server SandboxTemplate + SandboxWarmPool applied:
       kubectl apply -f agent_sandbox_deploy/sandboxtemplate.yaml
       kubectl apply -f agent_sandbox_deploy/sandboxwarmpool.yaml
  3. pip install openhands-workspace[agent-sandbox]
  4. export LLM_API_KEY=...   (and optionally LLM_MODEL, LLM_BASE_URL)

The LLM is called from inside the pod (the agent runs on the agent server), so the
cluster needs egress to your LLM endpoint.
"""

import os
import time

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Conversation,
    RemoteConversation,
    get_logger,
)
from openhands.tools.preset.default import get_default_agent
from openhands.workspace import AgentSandboxWorkspace


logger = get_logger(__name__)

# 1) LLM configuration
api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."

llm = LLM(
    usage_id="agent",
    model=os.getenv("LLM_MODEL", "gpt-5.5"),
    base_url=os.getenv("LLM_BASE_URL"),
    api_key=SecretStr(api_key),
)

# 2) Claim a pod from the warm pool. With a pre-warmed pool this returns in well
#    under a second; the workspace connects to the pod's agent server via
#    kubectl port-forward (the default 'port_forward' connection mode).
warmpool = os.getenv("AGENT_SANDBOX_WARMPOOL", "openhands-pool")
namespace = os.getenv("AGENT_SANDBOX_NAMESPACE", "default")
logger.info(f"Claiming a sandbox from warm pool {warmpool!r} in {namespace!r}...")
with AgentSandboxWorkspace(
    warmpool=warmpool,
    namespace=namespace,
    # Safety net: auto-delete the claim after 30 minutes if something leaks it.
    shutdown_after_seconds=30 * 60,
) as workspace:
    # 3) Create the agent
    agent = get_default_agent(llm=llm, cli_mode=True)

    # 4) Sanity-check the workspace with a direct command
    result = workspace.execute_command("echo 'Hello from agent-sandbox!' && pwd")
    logger.info(f"Command exit code: {result.exit_code}")
    logger.info(f"Output: {result.stdout}")

    # 5) Run a conversation
    conversation = Conversation(agent=agent, workspace=workspace)
    assert isinstance(conversation, RemoteConversation)
    try:
        logger.info(f"Conversation ID: {conversation.state.id}")
        conversation.send_message(
            "Read the current repo and write 3 facts about the project into FACTS.txt."
        )
        conversation.run()
        logger.info(f"Agent status: {conversation.state.execution_status}")

        # 6) Demonstrate native pause/resume. The pod is suspended (operatingMode
        #    -> Suspended) and its PVC is retained, then resumed for a follow-up.
        logger.info("Pausing the sandbox (operatingMode -> Suspended)...")
        workspace.pause()
        time.sleep(3)
        logger.info("Resuming the sandbox (operatingMode -> Running)...")
        workspace.resume()

        conversation.send_message("Great! Now append a 4th fact to FACTS.txt.")
        conversation.run()
        logger.info("Second task completed after resume.")

        cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
        print(f"EXAMPLE_COST: {cost}")
    finally:
        print("\nCleaning up conversation...")
        conversation.close()
