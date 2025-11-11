#!/usr/bin/env python3
"""
Planning Agent Workflow Example

This example demonstrates a two-stage workflow:
1. Planning Agent: Analyzes the task and creates a detailed implementation plan
2. Execution Agent: Implements the plan with full editing capabilities

The task: Create a Python web scraper that extracts article titles and URLs
from a news website, handles rate limiting, and saves results to JSON.
"""

import os
import tempfile
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Conversation
from openhands.sdk.llm import content_to_str
from openhands.tools.preset.default import get_default_agent
from openhands.tools.preset.planning import get_planning_agent


def get_event_content(event):
    """Extract content from an event."""
    if hasattr(event, "llm_message"):
        return "".join(content_to_str(event.llm_message.content))
    return str(event)


"""Run the planning agent workflow example."""

# Create a temporary workspace
workspace_dir = Path(tempfile.mkdtemp())
print(f"Working in: {workspace_dir}")

# Configure LLM
api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
base_url = os.getenv("LLM_BASE_URL")
llm = LLM(
    model=model,
    base_url=base_url,
    api_key=SecretStr(api_key),
    usage_id="agent",
)

# Task description
task = """
Create a Python web scraper with the following requirements:
- Scrape article titles and URLs from a news website
- Handle HTTP errors gracefully with retry logic
- Save results to a JSON file with timestamp
- Use requests and BeautifulSoup for scraping

Do NOT ask for any clarifying questions. Directly create your implementation plan.
"""

print("=" * 80)
print("PHASE 1: PLANNING")
print("=" * 80)

# Create Planning Agent with read-only tools
planning_agent = get_planning_agent(llm=llm)

# Create conversation for planning
planning_conversation = Conversation(
    agent=planning_agent,
    workspace=str(workspace_dir),
)

# Run planning phase
print("Planning Agent is analyzing the task and creating implementation plan...")
planning_conversation.send_message(
    f"Please analyze this web scraping task and create a detailed "
    f"implementation plan:\n\n{task}"
)
planning_conversation.run()

print("\n" + "=" * 80)
print("PLANNING COMPLETE")
print("=" * 80)
print(f"Implementation plan saved to: {workspace_dir}/PLAN.md")

print("\n" + "=" * 80)
print("PHASE 2: EXECUTION")
print("=" * 80)

# Create Execution Agent with full editing capabilities
execution_agent = get_default_agent(llm=llm, cli_mode=True)

# Create conversation for execution
execution_conversation = Conversation(
    agent=execution_agent,
    workspace=str(workspace_dir),
)

# Prepare execution prompt with reference to the plan file
execution_prompt = f"""
Please implement the web scraping project according to the implementation plan.

The detailed implementation plan has been created and saved at: {workspace_dir}/PLAN.md

Please read the plan from PLAN.md and implement all components according to it.

Create all necessary files, implement the functionality, and ensure everything
works together properly.
"""

print("Execution Agent is implementing the plan...")
execution_conversation.send_message(execution_prompt)
execution_conversation.run()

# Get the last message from the conversation
execution_result = execution_conversation.state.events[-1]

print("\n" + "=" * 80)
print("EXECUTION RESULT:")
print("=" * 80)
print(get_event_content(execution_result))

print("\n" + "=" * 80)
print("WORKFLOW COMPLETE")
print("=" * 80)
print(f"Project files created in: {workspace_dir}")

# List created files
print("\nCreated files:")
for file_path in workspace_dir.rglob("*"):
    if file_path.is_file():
        print(f"  - {file_path.relative_to(workspace_dir)}")

# Report cost
cost = llm.metrics.accumulated_cost
print(f"EXAMPLE_COST: {cost}")
