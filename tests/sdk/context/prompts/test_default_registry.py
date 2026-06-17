"""Phase 2 oracle: the default registry reproduces ``static_system_message``.

The registry canonicalizes inter-section spacing to a single blank line, while the
legacy template leaves 2--5 blanks around the guarded sections (un-trimmed ``{% if %}``
tags). :func:`_canonical_gaps` collapses exactly those ``</TAG>``..3+ blanks..``<TAG>``
boundaries, so every section *body* is asserted byte-for-byte; the registry's
single-blank policy is the only normalized difference.
"""

import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from uuid import uuid4

import pytest

from openhands.sdk.agent import Agent
from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.context.prompts.presets import create_registry
from openhands.sdk.context.prompts.section import Platform, PromptContext
from openhands.sdk.context.prompts.sections.dynamic import (
    AvailableSkillsSection,
    CustomSecretsSection,
    CustomSuffixSection,
    DateTimeSection,
    RepoContextSection,
)
from openhands.sdk.context.prompts.sections.static import (
    BrowserSection,
    EfficiencySection,
    ModelSpecificSection,
    RoleSection,
    SecurityRiskAssessmentSection,
    SecuritySection,
    SoulSection,
)
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.llm import LLM
from openhands.sdk.skills import KeywordTrigger, Skill
from openhands.sdk.workspace import LocalWorkspace

from .test_prompt_snapshot import (
    DYNAMIC_CONTEXT,
    FAMILY_MODELS,
    MATRIX,
    PLATFORM_CELL,
    Cell,
    _build_agent,
)


# The registry joins sections with one blank line; the legacy templates leave 2-5
# from un-trimmed `{% if %}`/`{% for %}` tags. Collapse runs of 3+ newlines that
# adjoin a tag (closing tag before, or opening tag after) -- i.e. the inter-section
# gaps. Within-section runs touch no tag (the SRA tiers, REPO_CONTEXT's `[BEGIN`
# loop), so they survive; the dynamic custom-suffix gaps adjoin exactly one tag and
# are still caught.
_GAP_AFTER_CLOSE: Final[re.Pattern[str]] = re.compile(r"(</[A-Z_]+>)\n{3,}")
_GAP_BEFORE_OPEN: Final[re.Pattern[str]] = re.compile(r"\n{3,}(<[A-Z_]+>)")
# The no-agent_context dynamic path stamps a render-time `datetime.now()` (a default
# AgentContext()), so the two renderers can't share the exact instant; mask that one
# line to assert the surrounding blocks byte-for-byte.
_DATETIME_LINE: Final[re.Pattern[str]] = re.compile(
    r"The current date and time is: [^\n]+"
)


def _canonical_gaps(text: str) -> str:
    text = _GAP_AFTER_CLOSE.sub(r"\1\n\n", text)
    return _GAP_BEFORE_OPEN.sub(r"\n\n\1", text)


def _mask_datetime(text: str) -> str:
    return _DATETIME_LINE.sub("The current date and time is: <NOW>", text)


@pytest.mark.parametrize("cell", MATRIX, ids=[c.id for c in MATRIX])
def test_registry_static_matches_legacy(cell: Cell) -> None:
    agent = _build_agent(cell)
    ctx = agent._build_prompt_context()
    static = create_registry().build(ctx).static
    assert static == _canonical_gaps(agent.static_system_message)


def test_registry_static_matches_legacy_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # refine() swaps bash->powershell on win32; ctx.platform is resolved from
    # sys.platform at build time, so both paths must agree byte-for-byte.
    monkeypatch.setattr(sys, "platform", "win32")
    agent = _build_agent(PLATFORM_CELL)
    ctx = agent._build_prompt_context()
    static = create_registry().build(ctx).static
    assert static == _canonical_gaps(agent.static_system_message)
    assert "powershell" in static


def test_default_registry_is_all_static() -> None:
    # With no dynamic data in the context, every dynamic section guards off, so the
    # dynamic block is empty.
    ctx = _ctx(
        security_policy_filename="security_policy.j2",
        llm_security_analyzer=True,
        model_family="anthropic_claude",
        cli_mode=True,
    )
    blocks = create_registry().build(ctx)
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


# --- dynamic tier: byte-for-byte vs the legacy suffix renderer -------------------


@pytest.mark.parametrize("cell", MATRIX, ids=[c.id for c in MATRIX])
def test_registry_dynamic_matches_legacy(cell: Cell) -> None:
    agent = _build_agent(cell)
    ctx = agent._build_prompt_context()
    registry = create_registry().build(ctx).dynamic or ""
    assert _canonical_gaps(registry) == _canonical_gaps(agent.dynamic_context or "")


def test_registry_dynamic_matches_legacy_with_secret_registry(tmp_path: Path) -> None:
    # get_dynamic_context(state) merges secrets from state.secret_registry -- the
    # <CUSTOM_SECRETS> path the bare dynamic_context property does not exercise.
    llm = LLM(model=FAMILY_MODELS["anthropic"], usage_id="snapshot-llm")
    agent = Agent(llm=llm, tools=[], agent_context=DYNAMIC_CONTEXT)
    state = ConversationState(
        id=uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
    )
    state.secret_registry.update_secrets({"GITHUB_TOKEN": "unused-in-snapshot"})

    additional = state.secret_registry.get_secret_infos()
    ctx = agent._build_prompt_context(additional_secret_infos=additional)
    registry = create_registry().build(ctx).dynamic or ""
    legacy = agent.get_dynamic_context(state) or ""
    assert _canonical_gaps(registry) == _canonical_gaps(legacy)


# --- dynamic tier: per-section unit tests ---------------------------------------


def test_datetime_section() -> None:
    section = DateTimeSection()
    assert section.guard(PromptContext(now="2025-01-01T00:00:00+00:00")) is True
    assert section.guard(PromptContext()) is False
    assert section.render(PromptContext(now="2025-01-01T00:00:00+00:00")) == (
        "<CURRENT_DATETIME>\n"
        "The current date and time is: 2025-01-01T00:00:00+00:00\n"
        "</CURRENT_DATETIME>"
    )


def test_repo_context_section() -> None:
    section = RepoContextSection()
    assert section.guard(PromptContext()) is False
    ctx = PromptContext(repo_skills=(("claude", "Anthropic-only repo guidance."),))
    out = section.render(ctx) or ""
    assert out.startswith("<REPO_CONTEXT>") and out.endswith("</REPO_CONTEXT>")
    assert "<UNTRUSTED_CONTENT>" in out
    assert "[BEGIN context from [claude]]" in out
    assert "Anthropic-only repo guidance." in out


def test_available_skills_section() -> None:
    section = AvailableSkillsSection()
    assert section.guard(PromptContext()) is False
    ctx = PromptContext(
        available_skills_prompt="<available_skills>X</available_skills>"
    )
    out = section.render(ctx) or ""
    assert out.startswith("<SKILLS>") and out.endswith("</SKILLS>")
    assert "invoke_skill" in out
    assert "<available_skills>X</available_skills>" in out


def test_custom_suffix_section() -> None:
    section = CustomSuffixSection()
    assert section.guard(PromptContext()) is False
    assert section.guard(PromptContext(custom_suffix="   ")) is False
    ctx = PromptContext(custom_suffix="Follow the repository's coding conventions.")
    assert section.render(ctx) == "Follow the repository's coding conventions."


def test_custom_secrets_section() -> None:
    section = CustomSecretsSection()
    assert section.guard(PromptContext()) is False
    out = section.render(PromptContext(secret_infos=(("GITHUB_TOKEN", None),))) or ""
    assert out.startswith("<CUSTOM_SECRETS>") and out.endswith("</CUSTOM_SECRETS>")
    assert "* **$GITHUB_TOKEN**" in out
    described = (
        section.render(PromptContext(secret_infos=(("API_KEY", "prod key"),))) or ""
    )
    assert "* **$API_KEY** - prod key" in described


def test_dynamic_sections_render_into_dynamic_block() -> None:
    ctx = PromptContext(
        now="2025-01-01T00:00:00+00:00",
        custom_suffix="Follow conventions.",
        secret_infos=(("GITHUB_TOKEN", None),),
    )
    blocks = create_registry().build(ctx)
    assert "<CURRENT_DATETIME>" in (blocks.dynamic or "")
    assert "<CUSTOM_SECRETS>" in (blocks.dynamic or "")
    assert "Follow conventions." in (blocks.dynamic or "")
    assert "<CURRENT_DATETIME>" not in blocks.static


def test_registry_dynamic_matches_legacy_with_available_skills() -> None:
    # Triggered skills land in <available_skills>/<SKILLS> -- the section the matrix
    # (legacy trigger=None skills -> REPO_CONTEXT) does not cover. Mirrors the
    # test_agent_context block assertions, asserted against registry output.
    agent_context = AgentContext(
        skills=[
            Skill(
                name="pdf-tools",
                content="Extract text from PDF files using pdftotext.",
                description="Extract text from PDF files.",
                source="pdf-tools.md",
                trigger=KeywordTrigger(keywords=["pdf", "extract"]),
            ),
        ],
        current_datetime="2025-01-01T00:00:00+00:00",
    )
    llm = LLM(model=FAMILY_MODELS["anthropic"], usage_id="snapshot-llm")
    agent = Agent(llm=llm, tools=[], agent_context=agent_context)
    ctx = agent._build_prompt_context()
    registry = create_registry().build(ctx).dynamic or ""
    assert "<SKILLS>" in registry
    assert "<name>pdf-tools</name>" in registry
    assert "<location>" not in registry  # invoke_skill is the only entry point
    assert _canonical_gaps(registry) == _canonical_gaps(agent.dynamic_context or "")


def test_build_prompt_context_formats_datetime_like_legacy() -> None:
    # ctx.now must match what get_formatted_datetime renders: a datetime object keeps
    # full ISO precision (NO minute-rounding), a pre-formatted string passes through
    # unchanged. Rounding here broke byte-for-byte parity for datetime callers (#3683).
    dt = datetime(2025, 1, 1, 12, 34, 56, 789000, tzinfo=UTC)
    agent = Agent(
        llm=LLM(model="claude-sonnet-4-5", usage_id="x"),
        tools=[],
        agent_context=AgentContext(current_datetime=dt),
    )
    assert agent._build_prompt_context().now == "2025-01-01T12:34:56.789000+00:00"

    agent_str = Agent(
        llm=LLM(model="claude-sonnet-4-5", usage_id="x"),
        tools=[],
        agent_context=AgentContext(current_datetime="2025-01-01T00:00:30+00:00"),
    )
    assert agent_str._build_prompt_context().now == "2025-01-01T00:00:30+00:00"


def test_registry_dynamic_matches_legacy_with_datetime_object() -> None:
    # End-to-end parity for the datetime-object path the matrix (string datetimes)
    # never exercises: the registry reproduces dynamic_context byte-for-byte, with the
    # datetime at full precision (the rounding bug surfaced only for datetime inputs).
    dt = datetime(2025, 1, 1, 12, 34, 56, 789000, tzinfo=UTC)
    llm = LLM(model=FAMILY_MODELS["anthropic"], usage_id="snapshot-llm")
    agent = Agent(llm=llm, tools=[], agent_context=AgentContext(current_datetime=dt))
    ctx = agent._build_prompt_context()
    registry = create_registry().build(ctx).dynamic or ""
    assert "The current date and time is: 2025-01-01T12:34:56.789000+00:00" in registry
    assert _canonical_gaps(registry) == _canonical_gaps(agent.dynamic_context or "")


def test_registry_dynamic_matches_legacy_no_context_secrets(tmp_path: Path) -> None:
    # No agent_context, but conversation secrets exist: get_dynamic_context builds a
    # default AgentContext() whose default current_datetime renders <CURRENT_DATETIME>
    # next to <CUSTOM_SECRETS>. The registry must reproduce BOTH blocks, not just the
    # secrets one (#3683 QA). The datetime is a render-time `now()` each path stamps
    # independently, so mask that single line and assert the rest byte-for-byte.
    llm = LLM(model=FAMILY_MODELS["anthropic"], usage_id="snapshot-llm")
    agent = Agent(llm=llm, tools=[])  # no agent_context
    state = ConversationState(
        id=uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
    )
    state.secret_registry.update_secrets({"API_KEY": "unused-in-snapshot"})
    additional = state.secret_registry.get_secret_infos()

    ctx = agent._build_prompt_context(additional_secret_infos=additional)
    registry = create_registry().build(ctx).dynamic or ""
    legacy = agent.get_dynamic_context(state) or ""

    assert "<CURRENT_DATETIME>" in registry
    assert "<CUSTOM_SECRETS>" in registry
    assert _mask_datetime(_canonical_gaps(registry)) == _mask_datetime(
        _canonical_gaps(legacy)
    )
