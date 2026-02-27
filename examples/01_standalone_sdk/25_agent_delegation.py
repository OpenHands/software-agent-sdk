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
from openhands.tools.delegate import (
    DelegateTool,
    DelegationVisualizer,
)
from openhands.tools.preset.default import get_default_tools


ONLY_RUN_SIMPLE_DELEGATION = False

logger = get_logger(__name__)

# Configure LLM and agent
llm = LLM(
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.environ.get("LLM_BASE_URL", None),
    usage_id="agent",
)

cwd = os.getcwd()

tools = get_default_tools(enable_browser=True)
tools.append(Tool(name=DelegateTool.name))

main_agent = Agent(
    llm=llm,
    tools=tools,
)
conversation = Conversation(
    agent=main_agent,
    workspace=cwd,
    visualizer=DelegationVisualizer(name="Delegator"),
)

conversation.send_message(
    "Forget about coding. Let's switch to travel planning. "
    "Let's plan a trip to London. I have two issues I need to solve: "
    "Lodging: what are the best areas to stay at while keeping budget in mind? "
    "Activities: what are the top 5 must-see attractions and hidden gems? "
    "Please use the delegation tools to handle these two tasks in parallel. "
    "Make sure the sub-agents use their own knowledge "
    "and dont rely on internet access. "
    "They should keep it short. After getting the results, merge both analyses "
    "into a single consolidated report.\n\n"
)
conversation.run()

conversation.send_message(
    "Ask the lodging sub-agent what it thinks about Covent Garden."
)
conversation.run()

# Report cost for simple delegation example
cost_simple = conversation.conversation_stats.get_combined_metrics().accumulated_cost
print(f"EXAMPLE_COST (simple delegation): {cost_simple}")

print("Simple delegation example done!", "\n" * 20)

if ONLY_RUN_SIMPLE_DELEGATION:
    # For CI: always emit the EXAMPLE_COST marker before exiting.
    print(f"EXAMPLE_COST: {cost_simple}")
    exit(0)


# -------- Agent Delegation Second Part: Built-in Agent Types (Explore + Bash) --------

main_agent = Agent(
    llm=llm,
    tools=[Tool(name=DelegateTool.name)],
)
conversation = Conversation(
    agent=main_agent,
    workspace=cwd,
    visualizer=DelegationVisualizer(name="Delegator (builtins)"),
)

builtin_task_message = (
    "Demonstrate SDK built-in sub-agent types. "
    "1) Spawn an 'explore' sub-agent and ask it to list the markdown files in "
    "openhands-sdk/openhands/sdk/subagent/builtins/ and summarize what each "
    "built-in agent type is for (based on the file contents). "
    "2) Spawn a 'bash' sub-agent and ask it to run `python --version` in the "
    "terminal and return the exact output. "
    "3) Merge both results into a short report. "
    "Do not use internet access."
)

print("=" * 100)
print("Demonstrating built-in agent delegation (explore + bash)...")
print("=" * 100)

conversation.send_message(builtin_task_message)
conversation.run()

# Report cost for builtin agent types example
cost_builtin = conversation.conversation_stats.get_combined_metrics().accumulated_cost
print(f"EXAMPLE_COST (builtin agents): {cost_builtin}")

print("Built-in agent delegation example done!", "\n" * 20)


# -------- Agent Delegation Third Part: User-Defined Agent Types --------


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

# Make the delegation tool available to the main agent
register_tool("DelegateTool", DelegateTool)

main_agent = Agent(
    llm=llm,
    tools=[Tool(name="DelegateTool")],
)
conversation = Conversation(
    agent=main_agent,
    workspace=cwd,
    visualizer=DelegationVisualizer(name="Delegator"),
)

task_message = (
    "Plan a 3-day London trip. "
    "1) Spawn two sub-agents: lodging_planner (hotel options) and "
    "activities_planner (itinerary). "
    "2) Ask lodging_planner for 3-4 central London hotel recommendations with "
    "neighborhoods, quick pros/cons, and transit notes by budget. "
    "3) Ask activities_planner for a concise 3-day itinerary with nearby stops, "
    "   food/coffee suggestions, and any ticket/reservation notes. "
    "4) Share both sub-agent results and propose a combined plan."
)

print("=" * 100)
print("Demonstrating London trip delegation (lodging + activities)...")
print("=" * 100)

conversation.send_message(task_message)
conversation.run()

conversation.send_message(
    "Ask the lodging sub-agent what it thinks about Covent Garden."
)
conversation.run()

# Report cost for user-defined agent types example
cost_user_defined = (
    conversation.conversation_stats.get_combined_metrics().accumulated_cost
)
print(f"EXAMPLE_COST (user-defined agents): {cost_user_defined}")

print("All done!")

# Full example cost report for CI workflow
print(f"EXAMPLE_COST: {cost_simple + cost_builtin + cost_user_defined}")
