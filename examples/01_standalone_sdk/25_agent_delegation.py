"""
Agent Delegation Example

This example demonstrates the agent delegation feature where a main agent
delegates tasks to sub-agents for parallel processing.
Each sub-agent runs independently and returns its results to the main agent,
which then merges both analyses into a single consolidated report.
"""

import os

from openhands.sdk import (
    LLM,
    Agent,
    AgentContext,
    Conversation,
    Tool,
    get_logger,
)
from openhands.sdk.context import Skill
from openhands.sdk.subagent import register_agent
from openhands.sdk.tool import register_tool
from openhands.tools import register_builtins_agents
from openhands.tools.delegate import (
    DelegateTool,
    DelegationVisualizer,
)


logger = get_logger(__name__)

# Configure LLM and agent
llm = LLM(
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.environ.get("LLM_BASE_URL", None),
    usage_id="agent",
)


def create_lodging_planner(llm: LLM) -> Agent:
    """Create a lodging planner focused on London stays."""
    skills = [
        Skill(
            name="lodging_planning",
            content=(
                "You specialize in finding great places to stay in London. "
                "Provide 3-4 hotel recommendations with neighborhoods, quick "
                "pros/cons, "
                "and notes on transit convenience. Keep options varied by budget."
            ),
            trigger=None,
        )
    ]
    return Agent(
        llm=llm,
        tools=[],
        agent_context=AgentContext(
            skills=skills,
            system_message_suffix="Focus only on London lodging recommendations.",
        ),
    )


def create_activities_planner(llm: LLM) -> Agent:
    """Create an activities planner focused on London itineraries."""
    skills = [
        Skill(
            name="activities_planning",
            content=(
                "You design concise London itineraries. Suggest 2-3 daily "
                "highlights, grouped by proximity to minimize travel time. "
                "Include food/coffee stops "
                "and note required tickets/reservations."
            ),
            trigger=None,
        )
    ]
    return Agent(
        llm=llm,
        tools=[],
        agent_context=AgentContext(
            skills=skills,
            system_message_suffix="Plan practical, time-efficient days in London.",
        ),
    )


# Register user-defined agent types (default agent type is always available)
register_agent(
    name="lodging_planner",
    factory_func=create_lodging_planner,
    description="Finds London lodging options with transit-friendly picks.",
)
register_agent(
    name="activities_planner",
    factory_func=create_activities_planner,
    description="Creates time-efficient London activity itineraries.",
)
register_builtins_agents()

# Make the delegation tool available to the main agent
register_tool("DelegateTool", DelegateTool)

main_agent = Agent(
    llm=llm,
    tools=[Tool(name="DelegateTool")],
)
conversation = Conversation(
    agent=main_agent,
    workspace=os.getcwd(),
    visualizer=DelegationVisualizer(name="Delegator"),
)

print("=" * 100)
print("Demonstrating London trip delegation (lodging + activities)...")
print("=" * 100)

conversation.send_message("""
Let's plan a trip to London. I have two specific areas to address:

Lodging: What are the best areas to stay in while keeping a budget in mind?
Activities: What are the top five must-see attractions and hidden gems?

Please use delegation tools to handle these two tasks in parallel.
Ensure the sub-agents use their own internal knowledge and do not
rely on internet access. Keep the responses concise.
Once you have the results, use the bash sub-agent to write a file
named london_trip_report.txt containing the findings in the working directory.
""")
conversation.run()

conversation.send_message(
    "Ask the lodging sub-agent what it thinks about Covent Garden."
)
conversation.run()

# Report cost for user-defined agent types example
cost_user_defined = (
    conversation.conversation_stats.get_combined_metrics().accumulated_cost
)
print(f"EXAMPLE_COST: {cost_user_defined}")

print("All done!")
