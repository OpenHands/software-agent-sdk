"""
Agent Delegation Example with Cybersecurity Expert + Default Agent

This example demonstrates agent delegation where a main agent can spawn specialized
sub-agents and delegate tasks to them. This simplified example shows:
- A cybersecurity expert agent for security analysis and vulnerability assessment
- A default agent for general programming and implementation tasks
- Collaboration between specialized and general-purpose agents

Each sub-agent runs independently with its own tools, skills, and system prompts,
then returns its results to the main agent for consolidation.
"""

import os

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Agent,
    AgentContext,
    Conversation,
    Tool,
    get_logger,
)
from openhands.sdk.context import Skill
from openhands.sdk.tool import register_tool
from openhands.tools.delegate import (
    DelegateTool,
    DelegationVisualizer,
    register_agent,
)
from openhands.tools.preset.default import get_default_tools


logger = get_logger(__name__)

# Configure LLM and agent
# You can get an API key from https://app.all-hands.dev/settings/api-keys
api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
llm = LLM(
    model=model,
    api_key=SecretStr(api_key),
    base_url=os.environ.get("LLM_BASE_URL", None),
    usage_id="agent",
)

cwd = os.getcwd()


# Register custom sub-agent types (demonstrates user extensibility)


def create_security_expert(llm: LLM) -> Agent:
    """Create a security expert agent with cybersecurity focus."""
    tools = get_default_tools(enable_browser=False)

    skills = [
        Skill(
            name="security_expertise",
            content=(
                "You are a cybersecurity expert. You always consider security "
                "implications, identify potential vulnerabilities, and recommend "
                "security best practices. You prioritize secure coding practices "
                "and threat mitigation."
            ),
            trigger=None,
        )
    ]

    agent_context = AgentContext(
        skills=skills,
        system_message_suffix=(
            "Always prioritize security in your analysis and recommendations."
        ),
    )

    return Agent(llm=llm, tools=tools, agent_context=agent_context)


# Note: The default agent is automatically registered by the delegation system
# Register the cybersecurity expert sub-agent
register_agent(
    name="security_expert",
    factory_func=create_security_expert,
    description="Expert in security analysis and vulnerability assessment",
)

register_tool("DelegateTool", DelegateTool)
tools = get_default_tools(enable_browser=False)
tools.append(Tool(name="DelegateTool"))

main_agent = Agent(
    llm=llm,
    tools=tools,
)
conversation = Conversation(
    agent=main_agent,
    workspace=cwd,
    visualizer=DelegationVisualizer(name="Delegator"),
)

# Task requiring both cybersecurity expert and default agent
task_message = (
    "I need to create a secure web application authentication system. "
    "Please spawn two agents to help: "
    "1. A security expert to analyze the authentication requirements and "
    "   identify security vulnerabilities "
    "2. A default agent to implement the actual authentication code and "
    "   handle the programming work "
    "Use the delegation tools to spawn these agents (security_expert and default), "
    "then delegate appropriate tasks to each. "
    "The security expert should focus on: password hashing best practices, "
    "session security, CSRF protection, and authentication flow vulnerabilities. "
    "The default agent should implement: user registration, login/logout "
    "functionality, password reset, and secure session management based on "
    "the security expert's recommendations. "
    "After getting their results, provide a complete implementation that follows "
    "security best practices."
)

print("=" * 100)
print("Demonstrating cybersecurity expert + default agent collaboration...")
print("=" * 100)

conversation.send_message(task_message)
conversation.run()

print("=" * 100)
print("Testing follow-up interaction with specific sub-agents...")
print("=" * 100)

conversation.send_message(
    "Ask the security expert sub-agent about password hashing best practices, "
    "and have the default agent implement a secure login function based on "
    "those recommendations."
)
conversation.run()
print("All done!")
