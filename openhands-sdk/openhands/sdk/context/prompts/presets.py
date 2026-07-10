"""Named :class:`PromptRegistry` presets -- ready-to-use section compositions.

``create_registry()`` selects a section composition over the same engine. The
``"default"`` preset registers the static-tier sections in the exact order
``agent/prompts/system_prompt.j2`` emitted them, so ``registry.build(ctx).static``
reproduces ``AgentBase.static_system_message``. The ``"planning"`` preset is a
distinct standalone composition (ported from ``system_prompt_planning.j2``) that
omits the default OpenHands sections. The dynamic-tier sections are **shared** --
repo/skills/suffix/secrets/datetime are preset-independent -- so a planning agent
with an ``agent_context`` still gets its dynamic block.
"""

from enum import StrEnum
from typing import Final

from openhands.sdk.context.prompts.registry import PromptRegistry
from openhands.sdk.context.prompts.section import PromptSection
from openhands.sdk.context.prompts.sections.dynamic import (
    AvailableSkillsSection,
    CustomSecretsSection,
    CustomSuffixSection,
    DateTimeSection,
    RepoContextSection,
    RuntimeServicesSection,
)
from openhands.sdk.context.prompts.sections.planning import PlanningSection
from openhands.sdk.context.prompts.sections.static import (
    BrowserSection,
    CodeQualitySection,
    EfficiencySection,
    EnvironmentSetupSection,
    ExternalServicesSection,
    FileSystemSection,
    MemorySection,
    ModelSpecificSection,
    ProblemSolvingSection,
    ProcessManagementSection,
    PullRequestsSection,
    RoleSection,
    SecurityRiskAssessmentSection,
    SecuritySection,
    SelfDocumentationSection,
    SoulSection,
    TroubleshootingSection,
    VersionControlSection,
)


__all__ = ["PromptPreset", "create_registry"]


class PromptPreset(StrEnum):
    """Names a :func:`create_registry` section composition."""

    DEFAULT = "default"
    PLANNING = "planning"


_DEFAULT_STATIC_SECTIONS: Final[tuple[PromptSection, ...]] = (
    SoulSection(),
    RoleSection(),
    MemorySection(),
    EfficiencySection(),
    FileSystemSection(),
    CodeQualitySection(),
    VersionControlSection(),
    PullRequestsSection(),
    ProblemSolvingSection(),
    SelfDocumentationSection(),
    SecuritySection(),  # guard: security_policy_filename set
    SecurityRiskAssessmentSection(),  # guard: llm_security_analyzer
    BrowserSection(),  # guard: ctx.enable_browser
    ExternalServicesSection(),
    EnvironmentSetupSection(),
    TroubleshootingSection(),
    ProcessManagementSection(),
    ModelSpecificSection(),  # guard: model_family resolved
)

_PLANNING_STATIC_SECTIONS: Final[tuple[PromptSection, ...]] = (PlanningSection(),)

_DYNAMIC_SECTIONS: Final[tuple[PromptSection, ...]] = (
    RepoContextSection(),  # guard: gated repo skills present
    AvailableSkillsSection(),  # guard: available_skills_prompt
    CustomSuffixSection(),  # guard: system_message_suffix
    RuntimeServicesSection(),  # guard: conversation runtime services
    CustomSecretsSection(),  # guard: secret_infos present
    # DateTimeSection is intentionally last: it is the only per-conversation
    # volatile value, so the stable dynamic content stays a cache-friendly prefix.
    DateTimeSection(),
)


def create_registry(preset: PromptPreset = PromptPreset.DEFAULT) -> PromptRegistry:
    """Build the section registry for ``preset``.

    ``DEFAULT`` is the standard OpenHands composition; ``PLANNING`` is the read-only
    analysis composition (no ``<SECURITY>``/``<SOUL>``/``<MEMORY>`` ...). Both share
    the dynamic tier. Sections are stateless, so the per-preset sequences are reused
    across calls.
    """
    match preset:
        case PromptPreset.PLANNING:
            static_sections = _PLANNING_STATIC_SECTIONS
        case PromptPreset.DEFAULT:
            static_sections = _DEFAULT_STATIC_SECTIONS
        case _:
            raise ValueError(f"Unknown prompt preset: {preset!r}")

    r = PromptRegistry()
    for section in (*static_sections, *_DYNAMIC_SECTIONS):
        r.register(section)
    return r
