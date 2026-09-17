"""Planning preset: the byte-for-byte port of ``system_prompt_planning.j2``.

The ``.j2`` was deleted once parity was confirmed, so the planning composition is now
pinned against a golden snapshot (regenerate with ``REGEN_PROMPT_SNAPSHOTS=1``). The
planning prompt is a single standalone section, so the unit tests exercise that one
section directly -- no Agent, no Jinja environment.
"""

import os
from pathlib import Path
from typing import Final

from openhands.sdk.context.prompts.presets import PromptPreset, create_registry
from openhands.sdk.context.prompts.section import Platform, PromptContext
from openhands.sdk.context.prompts.sections.planning import PlanningSection


SNAPSHOT_DIR: Final[Path] = Path(__file__).parent / "snapshots"
REGEN: Final[bool] = os.environ.get("REGEN_PROMPT_SNAPSHOTS") == "1"

# A representative plan_structure (the template's only substitution). Kept inline so the
# SDK test does not depend on openhands-tools' format_plan_structure().
PLAN_STRUCTURE: Final[str] = (
    "The plan must follow this structure exactly:\n\n"
    "1. OBJECTIVE\n   * Summarize the goal of the plan in one or two sentences.\n\n"
    "2. IMPLEMENTATION STEPS\n   * Provide a step-by-step plan for execution."
)


def _ctx(
    platform: Platform = Platform.LINUX, **template_kwargs: object
) -> PromptContext:
    return PromptContext(template_kwargs=template_kwargs, platform=platform)


def _check_snapshot(name: str, content: str) -> None:
    path = SNAPSHOT_DIR / f"{name}.txt"
    if REGEN:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return
    assert path.exists(), (
        f"Missing snapshot {path}. Regenerate with REGEN_PROMPT_SNAPSHOTS=1."
    )
    assert content == path.read_text(encoding="utf-8"), (
        f"Planning prompt for '{name}' diverged from its committed snapshot. If the "
        "change is intentional, regenerate with REGEN_PROMPT_SNAPSHOTS=1."
    )


def test_planning_registry_static_snapshot() -> None:
    ctx = _ctx(plan_structure=PLAN_STRUCTURE)
    blocks = create_registry(PromptPreset.PLANNING).build(ctx)
    # The planning preset registers no dynamic data here, so the dynamic block is empty.
    assert blocks.dynamic is None
    _check_snapshot("planning__default", blocks.static)


def test_planning_is_standalone_composition() -> None:
    ctx = _ctx(plan_structure=PLAN_STRUCTURE)
    static = create_registry(PromptPreset.PLANNING).build(ctx).static
    assert static.startswith("You are a Planning Agent")
    # Standalone: none of the default OpenHands sections leak in.
    for tag in ("<SOUL>", "<SECURITY>", "<MEMORY>", "<VERSION_CONTROL>", "<IMPORTANT>"):
        assert tag not in static
    for tag in ("<ROLE>", "<PLANNING_WORKFLOW>", "<PLAN_SCOPE>", "<PLAN_STRUCTURE>"):
        assert tag in static


def test_planning_section_is_unguarded() -> None:
    assert PlanningSection().guard(_ctx()) is True


def test_planning_section_substitutes_plan_structure() -> None:
    out = PlanningSection().render(_ctx(plan_structure="STEP ONE")) or ""
    assert "<PLAN_STRUCTURE>\nSTEP ONE\n</PLAN_STRUCTURE>" in out
    # An empty plan_structure reproduces the template's blank-line body verbatim.
    empty = PlanningSection().render(_ctx()) or ""
    assert empty.endswith("<PLAN_STRUCTURE>\n\n</PLAN_STRUCTURE>")


def test_planning_uses_glob_grep_and_is_platform_invariant() -> None:
    # The planning <EFFICIENCY> says "glob and grep" (not bash/git), so the Windows
    # shell substitution is a no-op and the prompt is identical across platforms.
    posix = PlanningSection().render(_ctx(platform=Platform.LINUX)) or ""
    windows = PlanningSection().render(_ctx(platform=Platform.WINDOWS)) or ""
    assert "glob and grep" in posix
    assert "bash" not in posix and "powershell" not in posix
    assert windows == posix


def test_planning_dynamic_tier_is_shared_with_default() -> None:
    # The dynamic tier is preset-independent: the same context yields the same block.
    ctx = PromptContext(
        now="2025-01-01T00:00:00+00:00",
        custom_suffix="Follow conventions.",
        secret_infos=(("GITHUB_TOKEN", None),),
        template_kwargs={"plan_structure": PLAN_STRUCTURE},
    )
    planning = create_registry(PromptPreset.PLANNING).build(ctx).dynamic
    default = create_registry(PromptPreset.DEFAULT).build(ctx).dynamic
    assert planning == default
    assert "<CURRENT_DATETIME>" in (planning or "")
