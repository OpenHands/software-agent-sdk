"""Example: Loading Sub-Agents from Markdown Files

This example demonstrates how to define sub-agents using Markdown files with
YAML frontmatter, load them with AgentDefinition, and register them for
delegation.

Agent Markdown files follow a simple format:
- YAML frontmatter with: name, description, tools, model (optional), color (optional)
- The Markdown body becomes the agent's system prompt
- <example> tags in the description help the main agent know when to delegate

The example_agents/ directory contains two agents:
- code-reviewer: Reviews code for quality, bugs, and best practices
- tech-writer: Writes and improves technical documentation
"""

import os
from pathlib import Path

from openhands.sdk import (
    LLM,
    Agent,
    Conversation,
    Tool,
    agent_definition_to_factory,
    get_logger,
    load_agents_from_dir,
    register_agent,
)
from openhands.sdk.subagent import AgentDefinition
from openhands.sdk.tool import register_tool
from openhands.tools.delegate import DelegateTool, DelegationVisualizer


logger = get_logger(__name__)

script_dir = Path(__file__).parent
agents_dir = script_dir / "agents"

print("Part 1: Loading Agent Definitions from Markdown")
print()

# Load each agent definition and inspect its fields
for md_file in sorted(agents_dir.glob("*.md")):
    agent_def = AgentDefinition.load(md_file)
    print(f"\nAgent: {agent_def.name}")
    print(f"  Description: {agent_def.description[:80]}...")
    print(f"  Model: {agent_def.model}")
    print(f"  Tools: {agent_def.tools}")
    print(f"  When-to-use examples: {agent_def.when_to_use_examples}")
    print(f"  System prompt length: {len(agent_def.system_prompt)} chars")
print()

print("Part 2: Registering and Using Agents with Delegation")
print()

LLM_API_KEY = os.getenv("LLM_API_KEY")
assert LLM_API_KEY is not None, "LLM API key not set"

model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
llm = LLM(
    model=model,
    api_key=LLM_API_KEY,
    base_url=os.getenv("LLM_BASE_URL"),
    usage_id="file-agents-demo",
)


agents_definition = load_agents_from_dir(agents_dir)

for agent_def in agents_definition:
    register_agent(
        name=agent_def.name,
        factory_func=agent_definition_to_factory(agent_def),
        description=agent_def.description,
    )
    print(f"Registered agent: {agent_def.name}")

# NOTE: In a real project, you can skip the manual loading above and instead
# place your .md files in .agents/agents/ at the project root, then call:
#
#   from openhands.sdk.subagent import register_file_agents
#   register_file_agents(project_dir)
#
# This automatically discovers and registers all agent definitions.

print(
    "Part 3: Set up the main (orchestrator) agent with "
    "the DelegateTool and start the conversation"
)
print()

# Set up the main (orchestrator) agent with the DelegateTool
register_tool("DelegateTool", DelegateTool)
main_agent = Agent(
    llm=llm,
    tools=[Tool(name="DelegateTool")],
)
conversation = Conversation(
    agent=main_agent,
    workspace=Path.cwd(),
    visualizer=DelegationVisualizer(name="Orchestrator"),
)

# Ask the main agent to delegate work to our file-based agents
task = (
    f"I have a Python file at {agents_dir}/code-reviewer.md. "
    "Please delegate to the code-reviewer agent and ask it to review that file "
    "for any issues. Then delegate to the tech-writer agent and ask it to "
    "suggest a short README paragraph describing what is the code-reviewer.md "
    "file doing. "
    "Finally, combine both results into a summary."
)

print("\nSending task to orchestrator...")
conversation.send_message(task)
conversation.run()

cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
print(f"\nTotal cost: ${cost:.4f}")
print(f"EXAMPLE_COST: {cost:.4f}")
