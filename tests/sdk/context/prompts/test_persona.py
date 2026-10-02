"""A persona replaces the persona-layer sections and keeps capability guidance."""

import re
from pathlib import Path
from typing import Any

from openhands.sdk.agent import Agent
from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.llm import LLM
from openhands.sdk.tool import Tool


PERSONA = "You are a ramen chef. Answer only cooking questions."
PERSONA_TAGS = {
    "SOUL",
    "ROLE",
    "EFFICIENCY",
    "FILE_SYSTEM_GUIDELINES",
    "CODE_QUALITY",
    "VERSION_CONTROL",
    "PULL_REQUESTS",
    "PROBLEM_SOLVING_WORKFLOW",
    "SELF_DOCUMENTATION",
    "ENVIRONMENT_SETUP",
    "TROUBLESHOOTING",
}
KEPT_TAGS = {
    "MEMORY",
    "SECURITY",
    "SECURITY_RISK_ASSESSMENT",
    "BROWSER_TOOLS",
    "EXTERNAL_SERVICES",
    "PROCESS_MANAGEMENT",
    "IMPORTANT",
}


def _agent(**kwargs: Any) -> Agent:
    return Agent(
        llm=LLM(model="claude-sonnet-4-5", api_key="k", usage_id="agent"),
        tools=[Tool(name="browser_tool_set")],
        agent_context=AgentContext(
            load_memory=True, system_message_suffix="Cite file paths."
        ),
        **kwargs,
    )


def _tags(text: str) -> set[str]:
    return set(re.findall(r"^<([A-Z_]+)>", text, re.M))


def test_default_prompt_has_persona_and_capability_sections() -> None:
    assert _tags(_agent().static_system_message) == PERSONA_TAGS | KEPT_TAGS


def test_persona_replaces_persona_sections_and_keeps_the_rest() -> None:
    static = _agent(persona=PERSONA).static_system_message

    assert static.startswith(PERSONA)
    assert _tags(static) == KEPT_TAGS


def test_persona_leaves_the_dynamic_block_unchanged() -> None:
    with_persona = _agent(persona=PERSONA).dynamic_context

    assert with_persona == _agent().dynamic_context
    assert "Cite file paths." in (with_persona or "")


def test_planning_preset_keeps_its_section_after_the_persona() -> None:
    static = _agent(
        persona=PERSONA,
        system_prompt_filename="system_prompt_planning.j2",
        system_prompt_kwargs={"plan_structure": "1. GOAL"},
    ).static_system_message

    assert static.startswith(PERSONA)
    assert "1. GOAL" in static


def test_inline_system_prompt_wins_over_persona() -> None:
    agent = _agent(persona=PERSONA, system_prompt="Verbatim.")

    assert agent.static_system_message == "Verbatim."


def test_custom_template_receives_the_persona(tmp_path: Path) -> None:
    template = tmp_path / "custom.j2"
    template.write_text("Persona: {{ persona }}")

    agent = _agent(persona=PERSONA, system_prompt_filename=str(template))

    assert agent.static_system_message == f"Persona: {PERSONA}"
