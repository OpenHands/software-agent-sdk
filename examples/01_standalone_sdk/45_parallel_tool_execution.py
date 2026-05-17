"""Example: Parallel tool execution with tool_concurrency_limit.

Demonstrates how setting tool_concurrency_limit on an Agent enables
concurrent tool execution within a single step. The orchestrator agent
delegates to multiple sub-agents in parallel, and each sub-agent itself
runs tools concurrently. This stress-tests the parallel execution system
end-to-end.
"""

import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path

from openhands.sdk import (
    LLM,
    Agent,
    AgentContext,
    Conversation,
    Tool,
    register_agent,
)
from openhands.sdk.context import Skill
from openhands.tools.delegate import DelegationVisualizer
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.task import TaskToolSet
from openhands.tools.terminal import TerminalTool


llm = LLM(
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL"),
    usage_id="parallel-tools-demo",
)


# --- Sub-agents ---


def create_code_analyst(llm: LLM) -> Agent:
    """Sub-agent that analyzes code structure."""
    return Agent(
        llm=llm,
        tools=[
            Tool(name=TerminalTool.name),
            Tool(name=FileEditorTool.name),
        ],
        tool_concurrency_limit=4,
        agent_context=AgentContext(
            skills=[
                Skill(
                    name="code_analysis",
                    content=(
                        "You analyze code structure. Use the terminal to count files, "
                        "lines of code, and list directory structure. Use the file "
                        "editor to read key files. Run multiple commands at once."
                    ),
                    trigger=None,
                )
            ],
            system_message_suffix="Be concise. Report findings in bullet points.",
        ),
    )


def create_doc_reviewer(llm: LLM) -> Agent:
    """Sub-agent that reviews documentation."""
    return Agent(
        llm=llm,
        tools=[
            Tool(name=TerminalTool.name),
            Tool(name=FileEditorTool.name),
        ],
        tool_concurrency_limit=4,
        agent_context=AgentContext(
            skills=[
                Skill(
                    name="doc_review",
                    content=(
                        "You review project documentation. Check README files, "
                        "docstrings, and inline comments. Use the terminal and "
                        "file editor to inspect files. Run multiple commands at once."
                    ),
                    trigger=None,
                )
            ],
            system_message_suffix="Be concise. Report findings in bullet points.",
        ),
    )


def create_dependency_checker(llm: LLM) -> Agent:
    """Sub-agent that checks project dependencies."""
    return Agent(
        llm=llm,
        tools=[
            Tool(name=TerminalTool.name),
            Tool(name=FileEditorTool.name),
        ],
        tool_concurrency_limit=4,
        agent_context=AgentContext(
            skills=[
                Skill(
                    name="dependency_check",
                    content=(
                        "You analyze project dependencies. Read pyproject.toml, "
                        "requirements files, and package configs. Summarize key "
                        "dependencies, their purposes, and any version constraints. "
                        "Run multiple commands at once."
                    ),
                    trigger=None,
                )
            ],
            system_message_suffix="Be concise. Report findings in bullet points.",
        ),
    )


# Register sub-agents
register_agent(
    name="code_analyst",
    factory_func=create_code_analyst,
    description="Analyzes code structure, file counts, and directory layout.",
)
register_agent(
    name="doc_reviewer",
    factory_func=create_doc_reviewer,
    description="Reviews documentation quality and completeness.",
)
register_agent(
    name="dependency_checker",
    factory_func=create_dependency_checker,
    description="Checks and summarizes project dependencies.",
)
# --- Orchestrator agent with parallel execution ---
main_agent = Agent(
    llm=llm,
    tools=[
        Tool(name=TaskToolSet.name),
        Tool(name=TerminalTool.name),
        Tool(name=FileEditorTool.name),
    ],
    tool_concurrency_limit=8,
)

persistence_dir = Path(tempfile.mkdtemp(prefix="parallel_example_"))

conversation = Conversation(
    agent=main_agent,
    workspace=Path.cwd(),
    visualizer=DelegationVisualizer(name="Orchestrator"),
    persistence_dir=persistence_dir,
)

print("=" * 80)
print("Parallel Tool Execution Stress Test")
print("=" * 80)

conversation.send_message("""
Analyze the current project by delegating to ALL THREE sub-agents IN PARALLEL:

1. code_analyst: Analyze the project structure (file counts, key directories)
2. doc_reviewer: Review documentation quality (README, docstrings)
3. dependency_checker: Check dependencies (pyproject.toml, requirements)

IMPORTANT: Delegate to all three agents at the same time using parallel tool calls.
Do NOT delegate one at a time - call all three delegate tools in a single response.

Once all three have reported back, write a consolidated summary to
project_analysis_report.txt in the working directory. The report should have
three sections (Code Structure, Documentation, Dependencies) with the key
findings from each sub-agent.
""")
conversation.run()

# --- Analyze persisted events for parallelism ---
#
# Walk the persistence directory to find all conversations (main + sub-agents).
# Each conversation stores events as event-*.json files under an events/ dir.
# We parse ActionEvent entries and group by llm_response_id — batches with 2+
# actions sharing the same response ID prove the LLM requested parallel calls
# and the executor handled them concurrently.

print("\n" + "=" * 80)
print("Parallelism Report")
print("=" * 80)


def _analyze_conversation(events_dir: Path) -> dict[str, list[str]]:
    """Return {llm_response_id: [tool_name, ...]} for multi-tool batches."""
    batches: dict[str, list[str]] = defaultdict(list)
    for event_file in sorted(events_dir.glob("event-*.json")):
        data = json.loads(event_file.read_text())
        if data.get("kind") == "ActionEvent" and "llm_response_id" in data:
            batches[data["llm_response_id"]].append(data.get("tool_name", "?"))
    return {rid: tools for rid, tools in batches.items() if len(tools) >= 2}


for events_dir in sorted(persistence_dir.rglob("events")):
    if not events_dir.is_dir():
        continue
    # Derive a label from the path (main conv vs sub-agent)
    rel = events_dir.parent.relative_to(persistence_dir)
    is_subagent = "subagents" in rel.parts
    label = "sub-agent" if is_subagent else "main agent"

    multi_batches = _analyze_conversation(events_dir)
    if multi_batches:
        for resp_id, tools in multi_batches.items():
            print(f"\n  {label} batch ({resp_id[:16]}...):")
            print(f"    Parallel tools: {tools}")
    else:
        print(f"\n  {label}: no parallel batches")

cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
print(f"\nTotal cost: ${cost:.4f}")
print(f"EXAMPLE_COST: {cost:.4f}")
