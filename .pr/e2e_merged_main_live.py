"""Live e2e after merging main: real model, real completion logs, offline report.

A GLM conversation with the terminal tool runs with log_completions=True; then
the logged tool schemas are inspected and scripts/prompt_composition_report.py
is run over the log folder. Run from the repo checkout:

    LLM_API_KEY=$(pass ai/zai) uv run python <this file> LOG_DIR
"""

import json
import os
import sys
import tempfile
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.tools.terminal import TerminalTool


log_dir = Path(sys.argv[1])
log_dir.mkdir(parents=True, exist_ok=True)

llm = LLM(
    model="openai/glm-4.7",
    api_key=SecretStr(os.environ["LLM_API_KEY"]),
    base_url="https://api.z.ai/api/coding/paas/v4",
    usage_id="e2e-prompt-composition",
    log_completions=True,
    log_completions_folder=str(log_dir),
)
agent = Agent(llm=llm, tools=[Tool(name=TerminalTool.name)])
conv = Conversation(
    agent=agent,
    workspace=tempfile.mkdtemp(prefix="e2e_prompt_composition_"),
    visualizer=None,
)
conv.send_message(
    "Run `echo composition-check` in the terminal and tell me the output."
)
conv.run()

print("=== LOGGED TOOLS ===")
for path in sorted(log_dir.glob("*.json")):
    data = json.loads(path.read_text())
    tools = data.get("tools") or []
    shapes = [
        (
            t.get("type"),
            (t.get("function") or {}).get("name"),
            sorted(
                ((t.get("function") or {}).get("parameters") or {}).get(
                    "properties", {}
                )
            )[:3],
        )
        for t in tools
    ]
    print(f"{path.name}: {shapes}")
print(f"final status: {conv.state.execution_status}")
