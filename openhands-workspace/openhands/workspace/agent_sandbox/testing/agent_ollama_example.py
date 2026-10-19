"""Full-agent e2e for AgentSandboxWorkspace with a local Ollama model (no API key).

Runs an OpenHands agent inside an agent-sandbox pod, driven by a local Ollama model,
and has it create a file with a bash command.

Two things make this work reliably with a small local model:
  * a model that emits structured tool calls. qwen2.5:7b and llama3.1:8b do;
    qwen2.5-coder and the :3b variants return tool calls as plain text, which the
    agent cannot act on;
  * a minimal, terminal-only agent. The default multi-tool agent overwhelms models
    at this size, and they plan, think and finish without ever executing.

Prerequisites (see TESTING.md): a cluster with the agent-sandbox controller and a
warm pool, an Ollama endpoint the *sandbox pod* can reach (in-cluster Service, or any
reachable host), and:

    pip install openhands-workspace[agent-sandbox]

Env:
    OLLAMA_URL               required, e.g. http://<ollama-clusterip>:11434
    OLLAMA_MODEL             default: qwen2.5
    AGENT_SANDBOX_WARMPOOL   default: openhands-pool
    AGENT_SANDBOX_NAMESPACE  default: default
"""

import os

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.tool import Tool
from openhands.tools.preset.default import register_default_tools
from openhands.tools.terminal import TerminalTool
from openhands.workspace import AgentSandboxWorkspace


WARMPOOL = os.environ.get("AGENT_SANDBOX_WARMPOOL", "openhands-pool")
NAMESPACE = os.environ.get("AGENT_SANDBOX_NAMESPACE", "default")


def main() -> None:
    llm = LLM(
        usage_id="agent",
        model="ollama_chat/" + os.environ.get("OLLAMA_MODEL", "qwen2.5"),
        base_url=os.environ["OLLAMA_URL"],
        api_key=SecretStr("ollama"),  # ignored by Ollama
        reasoning_effort="none",  # qwen2.5 has no "thinking" mode
        num_retries=8,
        timeout=900,  # small models on CPU can be slow per turn
    )
    register_default_tools(enable_browser=False)

    with AgentSandboxWorkspace(warmpool=WARMPOOL, namespace=NAMESPACE) as ws:
        print("connected:", ws.host)
        agent = Agent(
            llm=llm,
            tools=[Tool(name=TerminalTool.name)],
            system_prompt_kwargs={"cli_mode": True},
        )
        conv = Conversation(agent=agent, workspace=ws)
        conv.send_message(
            "Run this exact bash command: echo 'hi from the agent' > hello.txt"
        )
        conv.run()
        print("agent status:", conv.state.execution_status)

        out = ws.execute_command("cat /workspace/hello.txt")
        print(f"hello.txt (exit={out.exit_code}): {out.stdout!r}")
        assert out.exit_code == 0 and "hi from the agent" in out.stdout, out

    print("AGENT E2E PASSED")


if __name__ == "__main__":
    main()
