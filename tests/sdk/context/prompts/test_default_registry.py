"""Phase 2 oracle: the default registry reproduces ``static_system_message``.

The registry canonicalizes inter-section spacing to a single blank line, while the
legacy template leaves 2--5 blanks around the guarded sections (un-trimmed ``{% if %}``
tags). :func:`_canonical_gaps` collapses exactly those ``</TAG>``..3+ blanks..``<TAG>``
boundaries, so every section *body* is asserted byte-for-byte; the registry's
single-blank policy is the only normalized difference.
"""

import re
import sys
from typing import Final

import pytest

from openhands.sdk.context.prompts.default_registry import build_default_registry
from openhands.sdk.context.prompts.section import Platform, PromptContext
from openhands.sdk.context.prompts.sections.static import (
    BrowserSection,
    EfficiencySection,
    ModelSpecificSection,
    RoleSection,
    SecurityRiskAssessmentSection,
    SecuritySection,
    SoulSection,
)

from .test_prompt_snapshot import MATRIX, PLATFORM_CELL, Cell, _build_agent


# Collapse only inter-section gaps: a closing tag, 3+ newlines, an opening tag.
# Within-body blank runs aren't preceded by `</TAG>`, so they're untouched.
_GUARDED_GAP: Final[re.Pattern[str]] = re.compile(r"(</[A-Z_]+>)\n{3,}(<[A-Z_]+>)")


def _canonical_gaps(text: str) -> str:
    return _GUARDED_GAP.sub(r"\1\n\n\2", text)


@pytest.mark.parametrize("cell", MATRIX, ids=[c.id for c in MATRIX])
def test_registry_static_matches_legacy(cell: Cell) -> None:
    agent = _build_agent(cell)
    ctx = agent._build_prompt_context()
    static = build_default_registry().build(ctx).static
    assert static == _canonical_gaps(agent.static_system_message)


def test_registry_static_matches_legacy_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # refine() swaps bash->powershell on win32; ctx.platform is resolved from
    # sys.platform at build time, so both paths must agree byte-for-byte.
    monkeypatch.setattr(sys, "platform", "win32")
    agent = _build_agent(PLATFORM_CELL)
    ctx = agent._build_prompt_context()
    static = build_default_registry().build(ctx).static
    assert static == _canonical_gaps(agent.static_system_message)
    assert "powershell" in static


def test_default_registry_is_all_static() -> None:
    # No dynamic-tier sections are registered yet, so the dynamic block is empty.
    ctx = _ctx(
        security_policy_filename="security_policy.j2",
        llm_security_analyzer=True,
        model_family="anthropic_claude",
        cli_mode=True,
    )
    blocks = build_default_registry().build(ctx)
    assert blocks.dynamic is None
    assert blocks.static.startswith("<SOUL>\nYou are OpenHands agent")
    assert "<IMPORTANT>" in blocks.static


# --- per-section unit tests (no Agent, no Jinja environment) -------------------


def _ctx(
    platform: Platform = Platform.LINUX, **template_kwargs: object
) -> PromptContext:
    return PromptContext(template_kwargs=template_kwargs, platform=platform)


def test_static_text_section_renders_and_is_unguarded() -> None:
    out = RoleSection().render(_ctx())
    assert out is not None
    assert out.startswith("<ROLE>")
    assert out.endswith("</ROLE>")
    assert RoleSection().guard(_ctx()) is True


def test_soul_section_renders_custom_and_defaults() -> None:
    section = SoulSection()
    # Always emitted, like the template; falls back to the built-in identity.
    assert section.guard(_ctx()) is True
    default = section.render(_ctx())
    assert default == (
        "<SOUL>\nYou are OpenHands agent, a helpful AI assistant that can"
        " interact with a computer to solve tasks.\n</SOUL>"
    )
    custom = section.render(_ctx(soul_content="You are a tiny cat agent."))
    assert custom == "<SOUL>\nYou are a tiny cat agent.\n</SOUL>"


def test_refine_swaps_shell_term_on_windows_only() -> None:
    posix = EfficiencySection().render(_ctx(platform=Platform.LINUX)) or ""
    windows = EfficiencySection().render(_ctx(platform=Platform.WINDOWS)) or ""
    assert "bash" in posix and "powershell" not in posix
    assert "powershell" in windows and "bash" not in windows


def test_browser_section_guarded_on_enable_browser() -> None:
    assert BrowserSection().guard(_ctx(enable_browser=True)) is True
    assert BrowserSection().guard(_ctx(enable_browser=False)) is False
    assert BrowserSection().guard(_ctx()) is False


def test_security_section_guarded_on_policy_filename() -> None:
    assert SecuritySection().guard(_ctx(security_policy_filename="security_policy.j2"))
    assert not SecuritySection().guard(_ctx(security_policy_filename=""))
    assert not SecuritySection().guard(_ctx())


def test_security_risk_assessment_branches_on_cli_mode() -> None:
    section = SecurityRiskAssessmentSection()
    cli = section.render(_ctx(cli_mode=True)) or ""
    sandbox = section.render(_ctx(cli_mode=False)) or ""
    assert "Safe, read-only actions." in cli
    assert "Read-only actions inside sandbox." in sandbox
    # Unset cli_mode matches the template default(true) -> CLI branch.
    assert section.render(_ctx()) == cli


def test_security_risk_assessment_guarded_on_analyzer() -> None:
    assert SecurityRiskAssessmentSection().guard(_ctx(llm_security_analyzer=True))
    assert not SecurityRiskAssessmentSection().guard(_ctx())


def test_model_specific_selects_family_and_variant() -> None:
    section = ModelSpecificSection()
    anthropic = section.render(_ctx(model_family="anthropic_claude")) or ""
    gemini = section.render(_ctx(model_family="google_gemini")) or ""
    gpt5 = section.render(_ctx(model_family="openai_gpt", model_variant="gpt-5")) or ""
    assert (
        anthropic.startswith("<IMPORTANT>")
        and "follow the instructions exactly" in anthropic
    )
    assert "too proactive" in gemini
    assert "Communicate with the user" in gpt5


def test_model_specific_omitted_without_matching_body() -> None:
    section = ModelSpecificSection()
    assert section.guard(_ctx()) is False  # no model family resolved
    # Family resolved but no model_specific body -> nothing to add.
    assert section.render(_ctx(model_family="meta_llama")) is None
