"""
Demonstrate explicit `/condense` command and skill-triggered condensation.

This example uses Gemini 2.5 Pro for both the agent and the condenser. Set
GEMINI_API_KEY in your environment. The model id is `gemini-2.5-pro`.

Usage:
  GEMINI_API_KEY=... uv run python examples/01_standalone_sdk/15_condense_command.py
"""

import os

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Agent,
    Conversation,
    Event,
    LLMConvertibleEvent,
    get_logger,
)
from openhands.sdk.context import AgentContext, KeywordTrigger, Skill
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.tool import Tool
from openhands.tools.task_tracker import TaskTrackerTool


logger = get_logger(__name__)


def make_llm(usage_id: str) -> LLM:
    api_key = os.getenv("GEMINI_API_KEY")
    assert api_key, "Set GEMINI_API_KEY"
    return LLM(model="gemini-2.5-pro", api_key=SecretStr(api_key), usage_id=usage_id)


def main():
    # Minimal tools (no terminal needed)
    tools = [Tool(name=TaskTrackerTool.name)]
    llm_agent = make_llm("agent")
    condenser = LLMSummarizingCondenser(
        llm=make_llm("condenser"), max_size=10, keep_first=2
    )

    # Provide a simple knowledge skill that can trigger condensation by name
    condense_skill = Skill(
        name="condense",
        content="When activated, the conversation will be condensed.",
        trigger=KeywordTrigger(keywords=["/condense"]),
    )

    ctx = AgentContext(skills=[condense_skill])

    agent = Agent(llm=llm_agent, tools=tools, condenser=condenser, agent_context=ctx)

    llm_messages = []

    def cb(e: Event):
        if isinstance(e, LLMConvertibleEvent):
            llm_messages.append(e.to_llm_message())

    convo = Conversation(agent=agent, callbacks=[cb], workspace=".")

    convo.send_message("Start the conversation with some context.")
    convo.run()

    # Now request condensation explicitly
    print("Requesting condensation via /condense...")
    convo.send_message("/condense")
    convo.run()

    print("Finished. Total LLM messages collected:", len(llm_messages))


if __name__ == "__main__":
    main()
