"""Attach structured output schemas to existing tools."""

import os
from typing import cast

from pydantic import BaseModel, Field

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.event import ActionEvent
from openhands.sdk.tool import Tool, register_tool
from openhands.sdk.tool.builtins.finish import FinishTool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalAction, TerminalTool


class CommandRationale(BaseModel):
    purpose: str = Field(description="Why this command is being run, in one line.")
    expected_outcome: str = Field(
        description="What the assistant expects to observe from running it."
    )


class ProjectFacts(BaseModel):
    description: str = Field(description="One-paragraph description of the project.")
    facts: list[str] = Field(description="Three concise, distinct facts.")


register_tool("FinishTool", FinishTool)


llm = LLM(
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL", None),
)

agent = Agent(
    llm=llm,
    tools=[
        Tool(name=TerminalTool.name, params={"response_schema": CommandRationale}),
        Tool(name=FileEditorTool.name),
        Tool(name="FinishTool", params={"response_schema": ProjectFacts}),
    ],
    include_default_tools=["ThinkTool"],
)

conversation = Conversation(agent=agent, workspace=os.getcwd())
conversation.send_message(
    "Inspect the repo using terminal commands, then finish with three facts "
    "about the project."
)
conversation.run()

events = conversation.state.events
terminal_tool = agent.tools_map[TerminalTool.name]
finish_tool = agent.tools_map["finish"]

print("\n[Terminal commands with rationale]")

for event in events:
    if (
        isinstance(event, ActionEvent)
        and event.tool_name == TerminalTool.name
        and event.action is not None
    ):
        assert isinstance(event.action, TerminalAction)
        rationale = cast(CommandRationale, terminal_tool.parse_response(event.action))
        print(f"  $ {event.action.command}")
        print(f"    purpose:          {rationale.purpose}")
        print(f"    expected_outcome: {rationale.expected_outcome}")

facts = cast(ProjectFacts | None, finish_tool.parse_last_response(events))
if facts:
    print("\n[Finish]")
    print(f"  description: {facts.description}")
    for fact in facts.facts:
        print(f"  - {fact}")

cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
print(f"\nEXAMPLE_COST: {cost}")
