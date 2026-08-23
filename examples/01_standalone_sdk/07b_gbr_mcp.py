"""Build Remote Agent MCP attach example (stdio gbr-mcp).

Pairing stays on the host: gbr-agent pair && gbr-agent run.
This script only registers the loopback MCP server. Phone is spectator.
Protocol gbr/1. Not affiliated with xAI or SpaceX.
Never put mailbox keys in mcp_config or in git.
"""

import os

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Agent,
    Conversation,
    Event,
    LLMConvertibleEvent,
    get_logger,
)
from openhands.sdk.mcp import MCPServer
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer
from openhands.sdk.tool import Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


logger = get_logger(__name__)

# Configure LLM
api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
model = os.getenv("LLM_MODEL", "gpt-5.5")
base_url = os.getenv("LLM_BASE_URL")
llm = LLM(
    usage_id="agent",
    model=model,
    base_url=base_url,
    api_key=SecretStr(api_key),
)

cwd = os.getcwd()
tools = [
    Tool(name=TerminalTool.name),
    Tool(name=FileEditorTool.name),
]

# gbr-mcp stdio. Requires: gbr-agent run (Bot API on 127.0.0.1:8788).
# Clone https://github.com/LinespottingOrg/GrokBuildRemote-Agents and npm install
# in mcp/gbr-mcp, or set GBR_MCP_JS to the gbr-mcp.js path.
gbr_mcp_js = os.getenv(
    "GBR_MCP_JS",
    os.path.expanduser(
        "~/GrokBuildRemote-Agents/mcp/gbr-mcp/bin/gbr-mcp.js"
    ),
)
mcp_config = {
    "gbr": MCPServer(
        command="node",
        args=[gbr_mcp_js],
    ),
}

agent = Agent(
    llm=llm,
    tools=tools,
    mcp_config=mcp_config,
)

llm_messages = []


def conversation_callback(event: Event):
    if isinstance(event, LLMConvertibleEvent):
        llm_messages.append(event.to_llm_message())


conversation = Conversation(
    agent=agent,
    callbacks=[conversation_callback],
    workspace=cwd,
)
conversation.set_security_analyzer(LLMSecurityAnalyzer())

logger.info(
    "GBR MCP example: curl http://127.0.0.1:8788/health after `gbr-agent run`."
)
conversation.send_message(
    "Check http://127.0.0.1:8788/health (Build Remote Agent Bot API) and "
    "summarize whether gbr-agent is running. Do not print any secrets."
)
conversation.run()

print("=" * 100)
print("Conversation finished. Got the following LLM messages:")
for i, message in enumerate(llm_messages):
    print(f"Message {i}: {str(message)[:200]}")

cost = llm.metrics.accumulated_cost
print(f"EXAMPLE_COST: {cost}")
