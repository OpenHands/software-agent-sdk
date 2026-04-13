"""Structured output via ``response_schema``.

Attach a Pydantic model to *any* tool spec and the agent must populate those
fields when calling that tool. The schema is sent to the LLM as the tool's
JSON-schema parameters and validated on receipt.

Demonstrated here on two tools:
  - ``TerminalTool`` (existing SDK tool) — every command must come with a
    ``purpose`` and ``expected_outcome``, on top of the tool's own ``command``
    field. No subclassing required: the schema is merged in via the spec.
  - ``FinishTool`` (built-in) — the final answer comes back as a typed object.
"""

import os

from pydantic import BaseModel, Field

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.event import ActionEvent
from openhands.sdk.tool import Tool, register_tool
from openhands.sdk.tool.builtins.finish import FinishTool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


# --- Structured-output schemas ------------------------------------------------


class CommandRationale(BaseModel):
    """Forced-annotation schema attached to TerminalTool."""

    purpose: str = Field(description="Why this command is being run, in one line.")
    expected_outcome: str = Field(
        description="What the assistant expects to observe from running it."
    )


class ProjectFacts(BaseModel):
    summary: str = Field(description="One-paragraph summary of the project.")
    facts: list[str] = Field(description="Three concise, distinct facts.")


# Register FinishTool so we can attach a response_schema via Tool spec.
register_tool("FinishTool", FinishTool)


# --- Agent setup --------------------------------------------------------------


llm = LLM(
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL", None),
)

agent = Agent(
    llm=llm,
    tools=[
        # Existing tool, augmented with a forced-annotation schema:
        Tool(name=TerminalTool.name, params={"response_schema": CommandRationale}),
        Tool(name=FileEditorTool.name),
        Tool(name="FinishTool", params={"response_schema": ProjectFacts}),
    ],
    # Skip the auto-injected default FinishTool so our schema-bound one is used.
    include_default_tools=["ThinkTool"],
)

conversation = Conversation(agent=agent, workspace=os.getcwd())
conversation.send_message(
    "Inspect the repo using terminal commands, then finish with three facts "
    "about the project."
)
conversation.run()


# --- Recover typed outputs from any tool with a response_schema ---------------

events = conversation.state.events
terminal_tool = agent.tools_map[TerminalTool.name]
finish_tool = agent.tools_map["finish"]

# Every TerminalTool call now carries our annotation fields. Walk all events to
# show that the LLM populated them on every invocation.
print("\n[Terminal commands with rationale]")

for event in events:
    if (
        isinstance(event, ActionEvent)
        and event.tool_name == TerminalTool.name
        and event.action is not None
    ):
        rationale = terminal_tool.parse_response(event.action)
        # action.command is the tool's own field; rationale.* came from the schema.
        print(f"  $ {getattr(event.action, 'command', '?')}")
        print(f"    purpose:          {rationale.purpose}")  # type: ignore[attr-defined]
        print(f"    expected_outcome: {rationale.expected_outcome}")  # type: ignore[attr-defined]

# And the typed final answer:
facts = finish_tool.parse_last_response(events)
if facts:
    print("\n[Finish]")
    print(f"  summary: {facts.summary}")  # type: ignore[attr-defined]
    for fact in facts.facts:  # type: ignore[attr-defined]
        print(f"  - {fact}")

# Report cost
cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
print(f"\nEXAMPLE_COST: {cost}")
