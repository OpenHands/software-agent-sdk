"""Example: Using the GPT-5 preset with Codex-style update_plan tracking.

This example demonstrates the GPT-5 preset's planning surface. It asks the
agent to use `update_plan` before making a small workspace change, mirroring
examples that use `task_tracker` with the default preset.

Usage:
    export OPENAI_API_KEY=...  # or set LLM_API_KEY
    # Optionally set a model (we recommend a mini variant if available):
    # export LLM_MODEL=(
    #   "openai/gpt-5.2-mini"  # or fallback: "openai/gpt-5.1-mini" or "openai/gpt-5.1"
    # )

    uv run python examples/04_llm_specific_tools/03_gpt5_update_plan_preset.py
"""

import os

from openhands.sdk import LLM, Agent, Conversation
from openhands.tools.preset.gpt5 import get_gpt5_agent


api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
if not api_key:
    raise SystemExit("Please set OPENAI_API_KEY or LLM_API_KEY to run this example.")

model = os.getenv("LLM_MODEL", "openai/gpt-5.1")
base_url = os.getenv("LLM_BASE_URL", None)

llm = LLM(model=model, api_key=api_key, base_url=base_url)
agent: Agent = get_gpt5_agent(llm)

conversation = Conversation(agent=agent, workspace=os.getcwd())
conversation.send_message(
    "Before editing anything, use update_plan with three short steps. Then create "
    "or update GPT5_PLAN_DEMO.txt with a short checklist describing what you "
    "changed, and finish by marking every plan step completed."
)
conversation.run()

cost = llm.metrics.accumulated_cost
print(f"EXAMPLE_COST: {cost}")
