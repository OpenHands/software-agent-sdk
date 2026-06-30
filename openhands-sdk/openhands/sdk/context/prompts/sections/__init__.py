"""Concrete :class:`~openhands.sdk.context.prompts.section.PromptSection` units.

Sections are pure, synchronous functions of a :class:`PromptContext` -- no Jinja
environment, no filesystem loader, no ``render_template`` bootstrap -- so each one
unit-tests in isolation. The static tier (ported from ``system_prompt.j2``) lives
in :mod:`.static`; the standalone planning composition (ported from
``system_prompt_planning.j2``) in :mod:`.planning`.
"""

from openhands.sdk.context.prompts.sections.dynamic import (
    AvailableSkillsSection,
    CustomSecretsSection,
    CustomSuffixSection,
    DateTimeSection,
    RepoContextSection,
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


__all__ = [
    "AvailableSkillsSection",
    "BrowserSection",
    "CodeQualitySection",
    "CustomSecretsSection",
    "CustomSuffixSection",
    "DateTimeSection",
    "EfficiencySection",
    "EnvironmentSetupSection",
    "ExternalServicesSection",
    "FileSystemSection",
    "MemorySection",
    "ModelSpecificSection",
    "PlanningSection",
    "ProblemSolvingSection",
    "ProcessManagementSection",
    "PullRequestsSection",
    "RepoContextSection",
    "RoleSection",
    "SecurityRiskAssessmentSection",
    "SecuritySection",
    "SelfDocumentationSection",
    "SoulSection",
    "TroubleshootingSection",
    "VersionControlSection",
]
