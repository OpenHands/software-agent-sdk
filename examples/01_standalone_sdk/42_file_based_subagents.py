"""Example: Defining a sub-agent inline with AgentDefinition.

Defines a grammar-checker sub-agent using AgentDefinition, registers it,
and delegates work to it from an orchestrator agent.
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
from openhands.tools.delegate import DelegationVisualizer
from openhands.tools.task import TaskToolSet


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
    description=grammar_checker,
)

# 3. Set up the orchestrator agent with the task tool
llm = LLM(
    model=os.getenv("LLM_MODEL", "gpt-5.5"),
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL"),
    usage_id="file-agents-demo",
)

main_agent = Agent(
    llm=llm,
    tools=[Tool(name=TaskToolSet.name)],
)
conversation = Conversation(
    agent=main_agent,
    workspace=Path.cwd(),
    visualizer=DelegationVisualizer(name="Orchestrator"),
)

# 4. Ask the orchestrator to delegate to our agent
task = (
    "Please delegate to the grammar-checker agent and ask it to review "
    "the README.md file in search of grammatical errors."
)
conversation.send_message(task)
conversation.run()

cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
print(f"\nTotal cost: ${cost:.4f}")
print(f"EXAMPLE_COST: {cost:.4f}")
