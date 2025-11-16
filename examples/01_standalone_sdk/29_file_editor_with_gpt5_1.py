"""Example: Using FileEditor tool with GPT-5.1 models via direct OpenAI API.

This mirrors the ApplyPatch example but uses FileEditor to create/modify/delete
FACTS.txt. Useful for comparing Responses input/output behavior and logs.

Requirements:
- OPENAI_API_KEY in the environment (or LLM_API_KEY)
- Model: any openai/gpt-5.1* variant; default uses openai/gpt-5.1-codex-mini
"""

from __future__ import annotations

import os

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, get_logger
from openhands.sdk.tool import Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.task_tracker import TaskTrackerTool
from openhands.tools.terminal import TerminalTool


logger = get_logger(__name__)

api_key = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
assert api_key, "Set OPENAI_API_KEY (or LLM_API_KEY) in your environment."

model = os.getenv("LLM_MODEL", "openai/gpt-5.1-codex-mini")
assert model.startswith("openai/gpt-5.1"), "Model must be an openai gpt-5.1 variant"

llm = LLM(
    model=model,
    api_key=SecretStr(api_key),
    native_tool_calling=True,
    reasoning_summary=None,
    log_completions=True,
)

# Ensure registration
_ = (TerminalTool, TaskTrackerTool, FileEditorTool)

agent = Agent(
    llm=llm,
    tools=[Tool(name="terminal"), Tool(name="task_tracker"), Tool(name="file_editor")],
    system_prompt_kwargs={"cli_mode": True},
)

conversation = Conversation(agent=agent, workspace=os.getcwd())

prompt = (
    "You must use tools to perform all actions. Do not merely describe actions. "
    "Use the FileEditor tool to: "
    "1) create a FACTS.txt with a single line 'OpenHands SDK integrates tools.'; "
    "2) modify FACTS.txt by appending a second line 'FileEditor works.'; "
    "3) delete FACTS.txt using the terminal tool with: rm FACTS.txt."
)

conversation.send_message(prompt)
conversation.run()

print("Conversation finished.")
print(f"EXAMPLE_COST: {llm.metrics.accumulated_cost}")
