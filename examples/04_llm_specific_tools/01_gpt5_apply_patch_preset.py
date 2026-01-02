"""Example: Using GPT-5 preset with ApplyPatchTool for file editing.

This example demonstrates how to enable the GPT-5 preset, which swaps the
standard claude-style FileEditorTool for ApplyPatchTool.

Usage:
    export OPENAI_API_KEY=...  # or set LLM_API_KEY
    # Optionally set a model (we recommend a mini variant if available):
    # export LLM_MODEL=(
    #   "openai/gpt-5.2-mini"  # or fallback: "openai/gpt-5.1-mini" or "openai/gpt-5.1"
    # )

    uv run python examples/04_llm_specific_tools/01_gpt5_apply_patch_preset.py
"""

import os

from openhands.sdk import LLM, Agent, Conversation
from openhands.tools.preset.gpt5 import get_gpt5_agent


# Resolve API key from env
api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
if not api_key:
    raise SystemExit("Please set OPENAI_API_KEY or LLM_API_KEY to run this example.")

model = os.getenv("LLM_MODEL", "openai/gpt-5.1")
base_url = os.getenv("LLM_BASE_URL", None)

llm = LLM(model=model, api_key=api_key, base_url=base_url)

# Build an agent with the GPT-5 preset (ApplyPatchTool-based editing)
agent: Agent = get_gpt5_agent(llm)

# Run in the current working directory
cwd = os.getcwd()
conversation = Conversation(agent=agent, workspace=cwd)

conversation.send_message(
    "Create (or update) a file named GPT5_DEMO.txt at the repo root with "
    "two short lines describing this repository."
)
conversation.run()

# Report cost
cost = llm.metrics.accumulated_cost
print(f"EXAMPLE_COST: {cost}")
