"""Example: Defining a sub-agent inline with AgentDefinition.

Defines a grammar-checker sub-agent using AgentDefinition, registers it,
and delegates work to it from an orchestrator agent. The orchestrator then
asks the builtin default agent to judge the results.
"""

import os
from pathlib import Path

from openhands.sdk import (
    LLM,
    Agent,
    Conversation,
    Tool,
    agent_definition_to_factory,
    register_agent,
)
from openhands.sdk.subagent import AgentDefinition
from openhands.sdk.tool import register_tool
from openhands.tools.delegate import DelegateTool, DelegationVisualizer


# 1. Define a sub-agent using AgentDefinition
grammar_checker = AgentDefinition(
    name="grammar-checker",
    description="Checks documents for grammatical errors.",
    tools=["file_editor"],
    system_prompt="You are a grammar expert. Find and list grammatical errors.",
)

# 2. Register it in the delegate registry
register_agent(
    name=grammar_checker.name,
    factory_func=agent_definition_to_factory(grammar_checker),
    description=grammar_checker.description,
)

# 3. Set up the orchestrator agent with the DelegateTool
llm = LLM(
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL"),
    usage_id="file-agents-demo",
)

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

# 4. Ask the orchestrator to delegate to our agent
task = (
    "Please delegate to the grammar-checker agent and ask it to review "
    "the README.md file in search of grammatical errors.\n"
    "Then ask the default agent to judge the errors."
)
conversation.send_message(task)
conversation.run()

cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
print(f"\nTotal cost: ${cost:.4f}")
print(f"EXAMPLE_COST: {cost:.4f}")
