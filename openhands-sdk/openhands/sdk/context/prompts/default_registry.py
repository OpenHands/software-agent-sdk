"""The default :class:`PromptRegistry` -- today's system prompt as registered sections.

``build_default_registry()`` registers the static-tier sections in the exact order
``agent/prompts/system_prompt.j2`` emits them, so ``registry.build(ctx).static``
reproduces ``AgentBase.static_system_message``. The dynamic-tier sections
(``system_message_suffix.j2``) are appended separately.
"""

from openhands.sdk.context.prompts.registry import PromptRegistry
from openhands.sdk.context.prompts.sections.dynamic import (
    AvailableSkillsSection,
    CustomSecretsSection,
    CustomSuffixSection,
    DateTimeSection,
    RepoContextSection,
)
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


__all__ = ["build_default_registry"]


def build_default_registry() -> PromptRegistry:
    r = PromptRegistry()
    # static tier -- ported verbatim from system_prompt.j2 (#3610)
    r.register(SoulSection())
    r.register(RoleSection())
    r.register(MemorySection())
    r.register(EfficiencySection())
    r.register(FileSystemSection())
    r.register(CodeQualitySection())
    r.register(VersionControlSection())
    r.register(PullRequestsSection())
    r.register(ProblemSolvingSection())
    r.register(SelfDocumentationSection())
    r.register(SecuritySection())  # guard: security_policy_filename set
    r.register(SecurityRiskAssessmentSection())  # guard: llm_security_analyzer
    r.register(BrowserSection())  # guard: ctx.enable_browser
    r.register(ExternalServicesSection())
    r.register(EnvironmentSetupSection())
    r.register(TroubleshootingSection())
    r.register(ProcessManagementSection())
    r.register(ModelSpecificSection())  # guard: model_family resolved
    # dynamic tier -- ported verbatim from system_message_suffix.j2 (#3610)
    r.register(DateTimeSection())
    r.register(RepoContextSection())  # guard: gated repo skills present
    r.register(AvailableSkillsSection())  # guard: available_skills_prompt
    r.register(CustomSuffixSection())  # guard: system_message_suffix
    r.register(CustomSecretsSection())  # guard: secret_infos present
    return r
