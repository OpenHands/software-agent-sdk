"""
Claude Code-style Delegation Example

This example demonstrates the Claude Code-style delegation tools (Task, TaskOutput,
TaskStop) as an alternative to the existing DelegateTool (spawn/delegate pattern).

Key differences from 25_agent_delegation.py:
  - Task-based: one tool call = one subagent created and run
  - Three separate tools: task (launch), task_output (poll), task_stop (cancel)
  - Subagents are ephemeral — each task creates a fresh conversation

The example shows:
  1. Basic usage — a main agent delegates research tasks to subagents
  2. Custom agent types — registering specialized subagent factories
"""

import os

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, AgentContext, Conversation, Tool, get_logger
from openhands.sdk.context import Skill
from openhands.tools.delegate import DelegationVisualizer, register_agent
from openhands.tools.task import TaskToolSet


logger = get_logger(__name__)

# 1. LLM setup
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


# 2. Create custom agent type
def create_poet_agent(llm: LLM) -> Agent:
    """A subagent that responds only in rhyming verse."""
    return Agent(
        llm=llm,
        tools=[],
        agent_context=AgentContext(
            system_message_suffix=(
                "You are a poet. Always respond in rhyming verse. Never use prose."
            ),
        ),
    )


def create_critic_agent(llm: LLM) -> Agent:
    """A subagent that critiques ideas with dry wit."""
    return Agent(
        llm=llm,
        tools=[],
        agent_context=AgentContext(
            skills=[
                Skill(
                    name="criticism",
                    content=(
                        "You are a sharp but fair critic. Point out flaws and "
                        "strengths with dry humor. Keep responses under 3 sentences."
                    ),
                    trigger=None,
                )
            ],
            system_message_suffix="Be concise and witty in your critiques.",
        ),
    )


# 3. Register custom agent types so the task tool can reference them by name
register_agent(
    name="poet",
    factory_func=create_poet_agent,
    description="Responds to any prompt in rhyming verse.",
)
register_agent(
    name="critic",
    factory_func=create_critic_agent,
    description="Critiques ideas with dry wit and sharp observations.",
)

# 4. Create a fresh agent and conversation with the task tool set
agent = Agent(
    llm=llm,
    tools=[Tool(name=TaskToolSet.name)],
)
conversation = Conversation(
    agent=agent,
    workspace=cwd,
    visualizer=DelegationVisualizer(name="Director"),
)

conversation.send_message(
    """
I’d like two different perspectives on the concept of 'AI-generated poetry.'
1. Use the task tool with subagent_type='poet' to have a subagent write a
    short poem about an AI writing poetry.
2. Use the task tool with subagent_type='critic' to have a subagent critique
    the concept of AI-generated poetry.

Please run these tasks concurrently in the background. While they are processing,
let me know what your favorite pizza is. Once the tasks are complete, present
both responses together followed by your own brief commentary.
"""
)
conversation.run()
