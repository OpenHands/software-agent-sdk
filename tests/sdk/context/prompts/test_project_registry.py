"""The two project roles: the coordinator and worker prompt presets.

Both compositions are pinned against golden snapshots (regenerate with
``REGEN_PROMPT_SNAPSHOTS=1``), like the default and planning ones.
"""

import os
import re
from pathlib import Path
from typing import Final

import pytest

from openhands.sdk.agent import Agent
from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.context.prompts.presets import PromptPreset, create_registry
from openhands.sdk.context.prompts.section import Platform, PromptContext
from openhands.sdk.llm import LLM


SNAPSHOT_DIR: Final[Path] = Path(__file__).parent / "snapshots"
REGEN: Final[bool] = os.environ.get("REGEN_PROMPT_SNAPSHOTS") == "1"

PROJECT_ROLES = (PromptPreset.PROJECT_COORDINATOR, PromptPreset.PROJECT_WORKER)

CODING_TAGS = {
    "FILE_SYSTEM_GUIDELINES",
    "CODE_QUALITY",
    "VERSION_CONTROL",
    "PULL_REQUESTS",
    "PROBLEM_SOLVING_WORKFLOW",
    "ENVIRONMENT_SETUP",
    "TROUBLESHOOTING",
    "PROCESS_MANAGEMENT",
}


def _ctx(**kwargs: object) -> PromptContext:
    template_kwargs = {
        "model_family": "anthropic_claude",
        "security_policy_filename": "security_policy.j2",
        "llm_security_analyzer": True,
        "cli_mode": False,
    }
    project_context = kwargs.pop("project_context", None)
    persona = kwargs.pop("persona", None)
    if persona is not None:
        template_kwargs["persona"] = persona
    template_kwargs.update(kwargs)
    return PromptContext(
        template_kwargs=template_kwargs,
        platform=Platform.LINUX,
        project_context=project_context,  # type: ignore[arg-type]
    )


def _tags(text: str) -> set[str]:
    return set(re.findall(r"^<([A-Z_]+)>", text, re.M))


def _check_snapshot(name: str, content: str) -> None:
    path = SNAPSHOT_DIR / f"{name}.txt"
    if REGEN:
        path.write_text(content, encoding="utf-8")
        return
    assert path.exists(), f"Missing {path}. Regenerate with REGEN_PROMPT_SNAPSHOTS=1."
    assert content == path.read_text(encoding="utf-8"), (
        f"The {name} prompt diverged from its snapshot. If that is intended, "
        "regenerate with REGEN_PROMPT_SNAPSHOTS=1."
    )


@pytest.mark.parametrize("preset", PROJECT_ROLES, ids=lambda p: p.value)
def test_project_role_snapshot(preset: PromptPreset) -> None:
    static = create_registry(preset).build(_ctx()).static
    _check_snapshot(f"{preset.value}__anthropic", static)


def test_the_coordinator_routes_and_keeps_no_coding_workflow() -> None:
    static = create_registry(PromptPreset.PROJECT_COORDINATOR).build(_ctx()).static
    tags = _tags(static)

    assert {
        "SOUL",
        "ROLE",
        "MEMORY",
        "ROUTING",
        "BRIEFS",
        "COORDINATING_WORKFLOW",
        "WHAT_YOU_HEAR",
        "TALKING_TO_THE_PERSON",
        "SELF_DOCUMENTATION",
        "SECURITY",
    } <= tags
    assert not tags & CODING_TAGS
    assert "Only the person approves." in static
    # Live: a coordinator slept in its terminal between list calls, and ran
    # workers' tests in their worktrees through git stash.
    assert "After you dispatch work, end your turn." in static
    assert "never wait for it with sleep" in static
    assert "ask the worker to run them" in static


def test_the_worker_is_the_coding_agent_with_its_project_sections() -> None:
    default = _tags(create_registry(PromptPreset.DEFAULT).build(_ctx()).static)
    worker = create_registry(PromptPreset.PROJECT_WORKER).build(_ctx()).static

    assert _tags(worker) == (default - {"SELF_DOCUMENTATION"}) | {
        "WHO_IS_SPEAKING",
        "YOUR_REPORT",
    }
    # It asks in its report, where the person reads, not in a chat nobody watches.
    assert "consult with the user" not in worker
    assert "confirm with the user before proceeding" not in worker
    assert "unless the person or your brief asks for it" in worker
    assert "git worktree of a repository other workers share" in worker
    assert "Three memories, three purposes" in worker
    # Live: workers popped each other's stashes across shared worktrees.
    assert "Never use `git stash` here" in worker


def test_a_coordinator_persona_changes_its_voice_not_its_job() -> None:
    static = (
        create_registry(PromptPreset.PROJECT_COORDINATOR)
        .build(_ctx(persona="You are a dry, good-natured cat."))
        .static
    )

    assert static.startswith("You are a dry, good-natured cat.")
    assert "SOUL" not in _tags(static)
    assert {"ROLE", "ROUTING", "BRIEFS", "WHAT_YOU_HEAR"} <= _tags(static)


def test_a_worker_persona_keeps_who_is_speaking_and_the_report() -> None:
    static = (
        create_registry(PromptPreset.PROJECT_WORKER)
        .build(_ctx(persona="You are a technical writer."))
        .static
    )

    assert {"WHO_IS_SPEAKING", "YOUR_REPORT", "SECURITY"} <= _tags(static)
    assert not {"ROLE", "CODE_QUALITY"} & _tags(static)


@pytest.mark.parametrize("preset", [PromptPreset.DEFAULT, *PROJECT_ROLES])
def test_the_project_block_is_dynamic_and_only_when_set(preset: PromptPreset) -> None:
    registry = create_registry(preset)

    assert registry.build(_ctx()).dynamic is None
    blocks = registry.build(_ctx(project_context="Project: Docs"))
    assert blocks.dynamic == "<PROJECT>\nProject: Docs\n</PROJECT>"
    assert "Project: Docs" not in blocks.static


def _agent(**kwargs: object) -> Agent:
    return Agent(
        llm=LLM(model="claude-sonnet-4-5", api_key="k", usage_id="agent"),
        **kwargs,  # type: ignore[arg-type]
    )


def test_an_agent_selects_its_role_by_prompt_preset() -> None:
    coordinator = _agent(prompt_preset="project_coordinator")
    assert "<ROUTING>" in coordinator.static_system_message
    assert "<ROUTING>" not in _agent().static_system_message
    # An inline system prompt still wins.
    inline = _agent(prompt_preset="project_coordinator", system_prompt="Hi.")
    assert inline.static_system_message == "Hi."


def test_an_agent_renders_its_project_context() -> None:
    agent = _agent(
        prompt_preset="project_worker",
        agent_context=AgentContext(project_context="Project: Docs"),
    )

    assert agent.dynamic_context is not None
    assert agent.dynamic_context.startswith("<PROJECT>\nProject: Docs\n</PROJECT>")
