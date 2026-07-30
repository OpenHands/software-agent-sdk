"""Tests for the codex preset (tools, agent wiring, prompt rendering)."""

from __future__ import annotations

from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.llm import LLM
from openhands.tools.preset.codex import get_codex_agent, get_codex_tools


def _make_llm() -> LLM:
    return LLM(model="gpt-5.2-codex", usage_id="test")


# --- tool composition ---


def test_get_codex_tools_composition() -> None:
    """codex tools = terminal + apply_patch (no browser when off)."""
    tools = get_codex_tools(enable_browser=False)
    tool_names = {t.name for t in tools}

    assert "terminal" in tool_names
    assert "apply_patch" in tool_names
    # Default file_editor should NOT be present (apply_patch replaces it)
    assert "file_editor" not in tool_names
    # Browser tools should not be present when disabled
    assert "browser_tool_set" not in tool_names


# --- agent wiring ---


def test_get_codex_agent_uses_codex_template() -> None:
    agent = get_codex_agent(_make_llm(), cli_mode=True)
    assert agent.system_prompt_filename == "system_prompt_codex.j2"
    assert isinstance(agent.condenser, LLMSummarizingCondenser)
    assert agent.condenser.max_size == 480
    assert agent.condenser.target_size == 120
    assert agent.condenser.keep_first == 4
    assert agent.condenser.keep_last_user_turns == 3


def test_get_codex_agent_appends_extra_tools() -> None:
    """extra_tools (e.g. grep for KB search) are appended to the codex set."""
    from openhands.tools.preset.default import register_default_tools

    # grep lives in the default preset; register it before referencing it.
    register_default_tools(enable_browser=False)

    from openhands.sdk.tool import Tool

    agent = get_codex_agent(_make_llm(), cli_mode=True, extra_tools=[Tool(name="grep")])
    tool_names = {t.name for t in agent.tools}

    assert "terminal" in tool_names
    assert "apply_patch" in tool_names
    assert "grep" in tool_names


def test_get_codex_agent_threads_terminal_params() -> None:
    agent = get_codex_agent(
        _make_llm(),
        cli_mode=True,
        terminal_params={"sandbox_mode": "off"},
    )
    terminal = next(tool for tool in agent.tools if tool.name == "terminal")

    assert terminal.params == {"sandbox_mode": "off"}


def test_get_codex_agent_threads_prompt_kwargs() -> None:
    agent = get_codex_agent(
        _make_llm(),
        cli_mode=True,
        available_skills_prompt="<skill>demo</skill>",
        custom_instructions="DOMAIN RULES",
    )
    assert agent.system_prompt_kwargs["available_skills_prompt"] == (
        "<skill>demo</skill>"
    )
    assert agent.system_prompt_kwargs["custom_instructions"] == "DOMAIN RULES"


# --- prompt rendering (Jinja escape-hatch) ---


def test_codex_prompt_renders_key_sections() -> None:
    """The rendered codex prompt contains the ported codex sections + tool refs."""
    agent = get_codex_agent(_make_llm(), cli_mode=True)
    prompt = agent.static_system_message

    assert "## General" in prompt
    assert "## Editing constraints" in prompt
    assert "## Planning" in prompt
    assert "## `update_plan`" in prompt
    assert prompt.index("## Editing constraints") < prompt.index("## Planning")
    assert prompt.index("## Planning") < prompt.index("## Special user requests")
    assert "meaningful, logically ordered steps" in prompt
    assert "Do not let the plan go stale" in prompt
    assert "multi-stage Pyromind workflow" in prompt
    assert "complete plan snapshot" in prompt
    assert "## Presenting your work and final message" in prompt
    assert "## apply_patch" in prompt
    # Tool references must match the codex preset's actual tools.
    assert "update_plan" in prompt
    assert "apply_patch" in prompt
    assert "terminal" in prompt


def test_codex_prompt_injects_custom_instructions_and_skills() -> None:
    agent = get_codex_agent(
        _make_llm(),
        cli_mode=True,
        available_skills_prompt="<skill>demo-skill</skill>",
        custom_instructions="KNOWLEDGE BASE RULES",
    )
    prompt = agent.static_system_message

    assert "# Custom instructions" in prompt
    assert "KNOWLEDGE BASE RULES" in prompt
    assert "<SKILLS>" in prompt
    assert "invoke_skill" in prompt
    assert "resource list as an index, not a checklist" in prompt
    assert "zero resource reads is valid" in prompt
    assert "same resource should not be read twice" in prompt
    assert "<skill>demo-skill</skill>" in prompt


def test_codex_prompt_reuses_unchanged_tool_results() -> None:
    prompt = get_codex_agent(_make_llm(), cli_mode=True).static_system_message

    assert "Before repeating a tool call" in prompt
    assert "underlying resource and user-supplied inputs have not changed" in prompt
    assert "Agent-chosen changes to limits, pagination" in prompt


def test_codex_prompt_omits_optional_blocks_when_absent() -> None:
    """Without custom_instructions / skills, those blocks are not rendered."""
    agent = get_codex_agent(_make_llm(), cli_mode=True)
    prompt = agent.static_system_message

    assert "# Custom instructions" not in prompt
    assert "<SKILLS>" not in prompt
