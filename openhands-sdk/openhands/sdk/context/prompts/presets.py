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

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from openhands.sdk.context.prompts.registry import PromptRegistry
from openhands.sdk.context.prompts.section import (
    CacheTier,
    PromptContext,
    PromptSection,
)
from openhands.sdk.context.prompts.sections.dynamic import (
    AvailableSkillsSection,
    CustomSecretsSection,
    CustomSuffixSection,
    DateTimeSection,
    MemoryContextSection,
    RepoContextSection,
)
from openhands.sdk.context.prompts.sections.planning import PlanningSection
from openhands.sdk.context.prompts.sections.project import (
    BriefsSection,
    CoordinatingWorkflowSection,
    CoordinatorEfficiencySection,
    CoordinatorMemorySection,
    CoordinatorRoleSection,
    ProjectContextSection,
    ProjectSecuritySection,
    RoutingSection,
    TalkingToThePersonSection,
    WhatYouHearSection,
    WhoIsSpeakingSection,
    WorkerMemorySection,
    WorkerProblemSolvingSection,
    WorkerPullRequestsSection,
    WorkerRoleSection,
    WorkerTroubleshootingSection,
    WorkerVersionControlSection,
    YourReportSection,
)
from openhands.sdk.context.prompts.sections.static import (
    CodeQualitySection,
    EfficiencySection,
    EnvironmentSetupSection,
    ExternalServicesSection,
    FileSystemSection,
    MemorySection,
    ModelSpecificSection,
    PersonaSection,
    ProblemSolvingSection,
    ProcessManagementSection,
    PullRequestsSection,
    RoleSection,
    SecurityRiskAssessmentSection,
    SecuritySection,
    SelfDocumentationSection,
    SoulSection,
    ToolGuidanceSection,
    TroubleshootingSection,
    VersionControlSection,
)


__all__ = ["PromptPreset", "create_registry"]


class PromptPreset(StrEnum):
    """Names a :func:`create_registry` section composition."""

    DEFAULT = "default"
    PLANNING = "planning"
    PROJECT_COORDINATOR = "project_coordinator"
    PROJECT_WORKER = "project_worker"


@dataclass(frozen=True, slots=True)
class _ReplacedByPersona:
    """A persona-layer section: dropped when the agent supplies its own persona."""

    section: PromptSection
    name: str = field(init=False)
    cache_tier: CacheTier = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", self.section.name)
        object.__setattr__(self, "cache_tier", self.section.cache_tier)

    def guard(self, ctx: PromptContext) -> bool:
        return ctx.persona is None and self.section.guard(ctx)

    def render(self, ctx: PromptContext) -> str | None:
        return self.section.render(ctx)


_DEFAULT_STATIC_SECTIONS: Final[tuple[PromptSection, ...]] = (
    PersonaSection(),  # guard: persona set
    _ReplacedByPersona(SoulSection()),
    _ReplacedByPersona(RoleSection()),
    MemorySection(),
    _ReplacedByPersona(EfficiencySection()),
    _ReplacedByPersona(FileSystemSection()),
    _ReplacedByPersona(CodeQualitySection()),
    _ReplacedByPersona(VersionControlSection()),
    _ReplacedByPersona(PullRequestsSection()),
    _ReplacedByPersona(ProblemSolvingSection()),
    _ReplacedByPersona(SelfDocumentationSection()),
    SecuritySection(),  # guard: security_policy_filename set
    SecurityRiskAssessmentSection(),  # guard: llm_security_analyzer
    ToolGuidanceSection(),  # guard: loaded tools supply prompt_guidance
    ExternalServicesSection(),
    _ReplacedByPersona(EnvironmentSetupSection()),
    _ReplacedByPersona(TroubleshootingSection()),
    ProcessManagementSection(),
    ModelSpecificSection(),  # guard: model_family resolved
)
_PLANNING_STATIC_SECTIONS: Final[tuple[PromptSection, ...]] = (
    PersonaSection(),
    PlanningSection(),
)

# A project's coordinator routes work rather than doing it, so it keeps no
# coding-workflow sections. A persona replaces only its voice (``<SOUL>``),
# never the job.
_PROJECT_COORDINATOR_STATIC_SECTIONS: Final[tuple[PromptSection, ...]] = (
    PersonaSection(),
    _ReplacedByPersona(SoulSection()),
    CoordinatorRoleSection(),
    CoordinatorMemorySection(),
    CoordinatorEfficiencySection(),
    RoutingSection(),
    BriefsSection(),
    CoordinatingWorkflowSection(),
    WhatYouHearSection(),
    TalkingToThePersonSection(),
    SelfDocumentationSection(),
    ProjectSecuritySection(),
    SecurityRiskAssessmentSection(),
    ToolGuidanceSection(),
    ExternalServicesSection(),
    ModelSpecificSection(),
)
# A project's worker is the default coding agent, told who speaks to it, that
# its final message is a report, and to ask there instead of in the chat. A
# persona replaces the same sections as in the default preset; the project
# sections stay.
_PROJECT_WORKER_STATIC_SECTIONS: Final[tuple[PromptSection, ...]] = (
    PersonaSection(),
    _ReplacedByPersona(SoulSection()),
    _ReplacedByPersona(WorkerRoleSection()),
    WorkerMemorySection(),
    _ReplacedByPersona(EfficiencySection()),
    _ReplacedByPersona(FileSystemSection()),
    _ReplacedByPersona(CodeQualitySection()),
    _ReplacedByPersona(WorkerVersionControlSection()),
    _ReplacedByPersona(WorkerPullRequestsSection()),
    _ReplacedByPersona(WorkerProblemSolvingSection()),
    WhoIsSpeakingSection(),
    YourReportSection(),
    ProjectSecuritySection(),
    SecurityRiskAssessmentSection(),
    ToolGuidanceSection(),
    ExternalServicesSection(),
    _ReplacedByPersona(EnvironmentSetupSection()),
    _ReplacedByPersona(WorkerTroubleshootingSection()),
    ProcessManagementSection(),
    ModelSpecificSection(),
)

_DYNAMIC_SECTIONS: Final[tuple[PromptSection, ...]] = (
    RepoContextSection(),  # guard: gated repo skills present
    MemoryContextSection(),  # guard: resolved memory present
    ProjectContextSection(),  # guard: project_context present
    AvailableSkillsSection(),  # guard: available_skills_prompt
    CustomSuffixSection(),  # guard: system_message_suffix
    CustomSecretsSection(),  # guard: secret_infos present
    # DateTimeSection is intentionally last: it is the only per-conversation
    # volatile value, so the stable dynamic content stays a cache-friendly prefix.
    DateTimeSection(),
)


def create_registry(preset: PromptPreset = PromptPreset.DEFAULT) -> PromptRegistry:
    """Build the section registry for ``preset``.

    ``DEFAULT`` is the standard OpenHands composition; ``PLANNING`` is the read-only
    analysis composition (no ``<SECURITY>``/``<SOUL>``/``<MEMORY>`` ...);
    ``PROJECT_COORDINATOR`` and ``PROJECT_WORKER`` are the two roles of a project.
    All share the dynamic tier. Sections are stateless, so the per-preset
    sequences are reused across calls.
    """
    match preset:
        case PromptPreset.PLANNING:
            static_sections = _PLANNING_STATIC_SECTIONS
        case PromptPreset.DEFAULT:
            static_sections = _DEFAULT_STATIC_SECTIONS
        case PromptPreset.PROJECT_COORDINATOR:
            static_sections = _PROJECT_COORDINATOR_STATIC_SECTIONS
        case PromptPreset.PROJECT_WORKER:
            static_sections = _PROJECT_WORKER_STATIC_SECTIONS
        case _:
            raise ValueError(f"Unknown prompt preset: {preset!r}")

    r = PromptRegistry()
    for section in (*static_sections, *_DYNAMIC_SECTIONS):
        r.register(section)
    return r
