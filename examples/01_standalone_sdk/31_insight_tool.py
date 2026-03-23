"""Example demonstrating the Insight tool for session analysis.

This example shows how to use the Insight tool to analyze conversation
history and generate usage reports with optimization suggestions.

The Insight tool can be triggered by the agent when user types '/insight'.
"""

import os

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.tool import Tool
from openhands.tools.insight import InsightAction, InsightObservation, InsightTool
from openhands.tools.preset.default import get_default_tools


# Configure LLM
api_key: str | None = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."

llm: LLM = LLM(
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL", None),
    usage_id="agent",
    drop_params=True,
)

# Build tools list with Insight tool
tools = get_default_tools(enable_browser=False)

# Configure Insight tool with parameters
insight_params: dict[str, bool | str] = {}

# Add LLM configuration for Insight tool (uses same LLM as main agent)
insight_params["llm_model"] = llm.model
if llm.api_key:
    if isinstance(llm.api_key, SecretStr):
        insight_params["api_key"] = llm.api_key.get_secret_value()
    else:
        insight_params["api_key"] = llm.api_key
if llm.base_url:
    insight_params["api_base"] = llm.base_url

# Add Insight tool to the agent
tools.append(Tool(name=InsightTool.name, params=insight_params))

# Create agent with Insight capabilities
agent: Agent = Agent(llm=llm, tools=tools)

# Start conversation
cwd: str = os.getcwd()
PERSISTENCE_DIR = os.path.expanduser("~/.openhands")
CONVERSATIONS_DIR = os.path.join(PERSISTENCE_DIR, "conversations")
conversation = Conversation(
    agent=agent, workspace=cwd, persistence_dir=CONVERSATIONS_DIR
)

# Run insight analysis directly using execute_tool
print("\nRunning insight analysis on conversation history...")
try:
    insight_result = conversation.execute_tool(
        "insight",
        InsightAction(
            generate_html=True,
            suggest_skills=True,
            max_sessions=20,
        ),
    )

    # Cast to the expected observation type for type-safe access
    if isinstance(insight_result, InsightObservation):
        print(f"\n{insight_result.summary}")
        print(f"\nSessions analyzed: {insight_result.sessions_analyzed}")

        if insight_result.common_patterns:
            print("\nCommon Patterns:")
            for pattern in insight_result.common_patterns:
                print(f"  - {pattern}")

        if insight_result.bottlenecks:
            print("\nIdentified Bottlenecks:")
            for bottleneck in insight_result.bottlenecks:
                print(f"  - {bottleneck}")

        if insight_result.suggestions:
            print("\nOptimization Suggestions:")
            for i, suggestion in enumerate(insight_result.suggestions, 1):
                print(f"  {i}. {suggestion}")

        if insight_result.report_path:
            print(f"\nHTML Report generated: {insight_result.report_path}")
    else:
        print(f"Result: {insight_result.text}")

except KeyError as e:
    print(f"Tool not available: {e}")

print("\n" + "=" * 80)
print("Insight tool example completed!")
print("=" * 80)

# Report cost
cost = llm.metrics.accumulated_cost
print(f"EXAMPLE_COST: {cost}")


# Alternative: Use conversation to trigger insight via message
# Uncomment to test triggering via conversation:
#
# conversation.send_message("Please analyze my usage with /insight")
# conversation.run()
