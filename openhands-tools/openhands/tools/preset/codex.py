"""Codex preset configuration for OpenHands agents.

This preset aligns the agent's system prompt, tool set, and tool descriptions
with the Codex CLI (gpt-5.2-codex baseline). It uses ApplyPatchTool for file
edits (like the GPT-5 preset) and renders the ``system_prompt_codex.j2``
template via the Jinja escape-hatch on :class:`Agent`.

The AgentSkills mechanism is preserved: pass ``available_skills_prompt`` to
inject the ``<SKILLS>`` block so ``invoke_skill(...)`` keeps working.
"""

from openhands.sdk import Agent
from openhands.sdk.context import AgentContext
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.context.condenser.base import CondenserBase
from openhands.sdk.llm.llm import LLM
from openhands.sdk.logger import get_logger
from openhands.sdk.tool import Tool


logger = get_logger(__name__)

# Rendered via the Agent Jinja escape-hatch (see AgentBase.static_system_message).
_CODEX_SYSTEM_PROMPT_FILENAME = "system_prompt_codex.j2"


def register_codex_tools(enable_browser: bool = True) -> None:
    """Register the codex tool set (terminal, apply_patch, update_plan, browser)."""
    from openhands.tools.apply_patch import ApplyPatchTool
    from openhands.tools.terminal import TerminalTool
    from openhands.tools.update_plan import UpdatePlanTool

    logger.debug(f"Tool: {TerminalTool.name} registered.")
    logger.debug(f"Tool: {ApplyPatchTool.name} registered.")
    logger.debug(f"Tool: {UpdatePlanTool.name} registered.")

    if enable_browser:
        from openhands.tools.browser_use import BrowserToolSet

        logger.debug(f"Tool: {BrowserToolSet.name} registered.")


def get_codex_tools(enable_browser: bool = True) -> list[Tool]:
    """Get the codex tool specifications using ApplyPatchTool for edits.

    Args:
        enable_browser: Whether to include browser tools.
    """
    register_codex_tools(enable_browser=enable_browser)

    from openhands.tools.apply_patch import ApplyPatchTool
    from openhands.tools.terminal import TerminalTool
    from openhands.tools.update_plan import UpdatePlanTool

    tools: list[Tool] = [
        Tool(name=TerminalTool.name),
        Tool(name=ApplyPatchTool.name),
        Tool(name=UpdatePlanTool.name),
    ]
    if enable_browser:
        from openhands.tools.browser_use import BrowserToolSet

        tools.append(Tool(name=BrowserToolSet.name))
    return tools


def get_codex_condenser(llm: LLM) -> CondenserBase:
    """Get the default condenser for the codex preset."""
    return LLMSummarizingCondenser(
        llm=llm,
        max_size=480,
        target_size=120,
        keep_first=4,
        keep_last_user_turns=3,
    )


def get_codex_agent(
    llm: LLM,
    cli_mode: bool = False,
    available_skills_prompt: str | None = None,
    custom_instructions: str | None = None,
    extra_tools: list[Tool] | None = None,
    agent_context: AgentContext | None = None,
    terminal_params: dict[str, object] | None = None,
) -> Agent:
    """Get an agent aligned with Codex (gpt-5.2-codex) prompt + tools.

    Args:
        llm: The LLM configuration for the agent.
        cli_mode: When True, browser tools are disabled.
        available_skills_prompt: Optional pre-rendered skills listing injected
            into the ``<SKILLS>`` block so ``invoke_skill(...)`` remains usable.
        custom_instructions: Optional domain instructions layered on top of the
            codex base prompt (rendered into the ``# Custom instructions`` block).
        extra_tools: Optional extra tools appended to the codex tool set (e.g.
            ``Tool(name="grep")`` for knowledge-base search).
        agent_context: Optional :class:`AgentContext`. When it carries
            AgentSkills-format skills, the SDK auto-attaches ``InvokeSkillTool``
            so the model can actually call ``invoke_skill(...)`` (the prompt text
            alone does not attach the tool).
        terminal_params: Optional parameters for the terminal tool. This lets a
            domain preset narrow terminal behavior without changing the default
            Codex tool contract.
    """
    tools = get_codex_tools(enable_browser=not cli_mode)
    if terminal_params:
        tools = [
            Tool(name=tool.name, params=terminal_params)
            if tool.name == "terminal"
            else tool
            for tool in tools
        ]
    if extra_tools:
        tools.extend(extra_tools)
    agent = Agent(
        llm=llm,
        tools=tools,
        agent_context=agent_context,
        system_prompt_filename=_CODEX_SYSTEM_PROMPT_FILENAME,
        system_prompt_kwargs={
            "cli_mode": cli_mode,
            "available_skills_prompt": available_skills_prompt,
            "custom_instructions": custom_instructions,
        },
        condenser=get_codex_condenser(
            llm=llm.model_copy(update={"usage_id": "condenser"})
        ),
    )
    return agent
