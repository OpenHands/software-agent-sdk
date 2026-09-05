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

## Install (SHA-256)

Pin GitHub Release **v0.6.0** and verify `SHA256SUMS`. Website `install.sh` / `install.ps1` abort on mismatch.

https://github.com/LinespottingOrg/GrokBuildRemote-Agents/releases/tag/v0.6.0
https://github.com/LinespottingOrg/GrokBuildRemote-Agents/blob/main/docs/PINNED-INSTALL.md

```
96cef605d3e030ccef99d27ea6240e0d3b668dd045e6b5b9e585c9fd03c6ef23  gbr-agent-darwin-amd64
de7e065ef2cf6877b3b2cd04679a67b627f876337f529247e236204543e4062c  gbr-agent-darwin-arm64
a50a5c41993e6531a3b477eb409ccc845212bf541384dc803061c80657f86719  gbr-agent-linux-amd64
5bfd22c7110234942c4c02ff8154b836d0af45a9422c178a4f52010187d40061  gbr-agent-linux-arm64
f773b89fd31310172b756e0593e0f3b2382b0a3440af2a7d0a8b3073b0c23e27  gbr-agent-windows-amd64.exe
8fb9efcbc7e2ac91c11964944bf0f45e31bb23f4356d9dcb4b305d7cb9b0fe8c  gbr-agent-windows-arm64.exe
```

```bash
VER=v0.6.0
BASE=https://github.com/LinespottingOrg/GrokBuildRemote-Agents/releases/download/$VER
# swap darwin-arm64 for your OS/arch
curl -fsSL -o gbr-agent-darwin-arm64 "$BASE/gbr-agent-darwin-arm64"
curl -fsSL -o SHA256SUMS "$BASE/SHA256SUMS"
shasum -a 256 -c SHA256SUMS --ignore-missing
gbr-agent pair && gbr-agent run
```

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

## What the phone sees

**Terminal windows** on this PC (machine-wide mailbox). Not headless OpenCode / CodeNomad sidecar / Electron. `:8788` in a sidecar is Bot API JSON, not a transcript.

https://github.com/LinespottingOrg/GrokBuildRemote-Agents/blob/main/docs/WHAT-THE-PHONE-SEES.md
https://grokbuildremote.com/integrations.html
