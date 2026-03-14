"""Planning agent preset configuration."""

from openhands.sdk import Agent
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.llm.llm import LLM
from openhands.sdk.logger import get_logger
from openhands.sdk.tool import Tool


logger = get_logger(__name__)


# Plan structure definition as list of (section_title, section_description) tuples
PLAN_STRUCTURE: list[tuple[str, str]] = [
    (
        "OBJECTIVE",
        (
            "* Summarize the goal of the plan in one or two sentences.\n"
            "* Restate the problem in clear operational terms."
        ),
    ),
    (
        "CONTEXT SUMMARY",
        (
            "* Briefly describe the relevant system components, files, or data involved.\n"  # noqa: E501
            "* Mention any dependencies or constraints (technical, organizational, or external)."  # noqa: E501
        ),
    ),
    (
        "APPROACH OVERVIEW",
        (
            "* Outline the chosen approach at a high level.\n"
            "* Mention why it was selected (short rationale) if alternatives were considered."  # noqa: E501
        ),
    ),
    (
        "IMPLEMENTATION STEPS",
        (
            "* Provide a step-by-step plan for execution.\n"
            "* Each step should include:\n"
            "  - a **goal** (what this step accomplishes),\n"
            "  - a **method** (how to do it, briefly),\n"
            "  - and optionally a **reference** (file, module, or function impacted)."
        ),
    ),
    (
        "TESTING AND VALIDATION",
        (
            "* Describe how the implementation can be verified or validated.\n"
            "* This section should describe what success looks like — expected outputs, behaviors, or conditions."  # noqa: E501
        ),
    ),
]


def format_plan_structure() -> str:
    """Format the PLAN_STRUCTURE into a string for system prompt injection.

    Returns:
        Formatted plan structure string ready for system prompt.
    """

    if not PLAN_STRUCTURE:
        return ""

    formatted_sections = []
    for i, (title, description) in enumerate(PLAN_STRUCTURE, 1):
        # Split description into lines and indent each line properly
        description_lines = description.split("\n")
        indented_description = "\n   ".join(description_lines)
        formatted_sections.append(f"{i}. {title}\n   {indented_description}")

    return "The plan must follow this structure exactly:\n\n" + "\n\n".join(
        formatted_sections
    )


def get_plan_headers() -> str:
    """Get plan section headers for initializing PLAN.md.

    Returns:
        Plan headers as markdown string.
    """
    headers = []
    for i, (title, _) in enumerate(PLAN_STRUCTURE, 1):
        headers.append(f"# {i}. {title}\n")

    return "\n".join(headers)


def register_planning_tools() -> None:
    """Register the planning agent tools."""
    # Tools are now automatically registered when imported
    from openhands.tools.glob import GlobTool  # noqa: F401
    from openhands.tools.grep import GrepTool  # noqa: F401
    from openhands.tools.planning_file_editor import (
        PlanningFileEditorTool,  # noqa: F401
    )

    logger.debug("Tool: GlobTool registered.")
    logger.debug("Tool: GrepTool registered.")
    logger.debug("Tool: PlanningFileEditorTool registered.")


def get_planning_tools(plan_path: str | None = None) -> list[Tool]:
    """Get the planning agent tool specifications.

    Args:
        plan_path: Optional absolute path to PLAN.md file. If provided, will be
            passed to PlanningFileEditorTool via params.

    Returns:
        List of tools optimized for planning and analysis tasks, including
        file viewing and PLAN.md editing capabilities for advanced
        code discovery and navigation.
    """
    register_planning_tools()

    # Import tools to access their name attributes
    from openhands.tools.glob import GlobTool
    from openhands.tools.grep import GrepTool
    from openhands.tools.planning_file_editor import PlanningFileEditorTool

    # Build params for PlanningFileEditorTool if plan_path is provided
    planning_tool_params = {}
    if plan_path:
        planning_tool_params["plan_path"] = plan_path

    return [
        Tool(name=GlobTool.name),
        Tool(name=GrepTool.name),
        Tool(name=PlanningFileEditorTool.name, params=planning_tool_params),
    ]


def get_planning_condenser(llm: LLM) -> LLMSummarizingCondenser:
    """Get a condenser optimized for planning workflows.

    Args:
        llm: The LLM to use for condensation.

    Returns:
        A condenser configured for planning agent needs.
    """
    # Planning agents may need more context for thorough analysis
    condenser = LLMSummarizingCondenser(
        llm=llm,
        max_size=100,  # Larger context window for planning
        keep_first=6,  # Keep more initial context
    )
    return condenser


def get_planning_agent(
    llm: LLM,
) -> Agent:
    """Get a configured planning agent.

    Args:
        llm: The LLM to use for the planning agent.
        enable_security_analyzer: Whether to enable security analysis.

    Returns:
        A fully configured planning agent with read-only file operations and
        command-line capabilities for comprehensive code discovery.
    """
    tools = get_planning_tools()

    agent = Agent(
        llm=llm,
        tools=tools,
        system_prompt_filename="system_prompt_planning.j2",
        system_prompt_kwargs={"plan_structure": format_plan_structure()},
        condenser=get_planning_condenser(
            llm=llm.model_copy(update={"usage_id": "planning_condenser"})
        ),
    )

    return agent
