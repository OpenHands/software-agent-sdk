from __future__ import annotations

import pathlib
from collections.abc import Mapping
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from openhands.sdk.context.prompts import render_template
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.llm.utils.model_prompt_spec import get_model_prompt_spec
from openhands.sdk.logger import get_logger
from openhands.sdk.secret import SecretSource, SecretValue
from openhands.sdk.skills import (
    Skill,
    SkillKnowledge,
    load_available_skills,
    to_prompt,
)
from openhands.sdk.skills.skill import DEFAULT_MARKETPLACE_PATH


logger = get_logger(__name__)

PROMPT_DIR = pathlib.Path(__file__).parent / "prompts" / "templates"


class AgentContext(BaseModel):
    """Central structure for managing prompt extension.

    AgentContext unifies all the contextual inputs that shape how the system
    extends and interprets user prompts. It combines both static environment
    details and dynamic, user-activated extensions from skills.

    Specifically, it provides:
    - **Repository context / Repo Skills**: Information about the active codebase,
      branches, and repo-specific instructions contributed by repo skills.
    - **Runtime context**: Current execution environment (hosts, working
      directory, secrets, date, etc.).
    - **Conversation instructions**: Optional task- or channel-specific rules
      that constrain or guide the agent’s behavior across the session.
    - **Knowledge Skills**: Extensible components that can be triggered by user input
      to inject knowledge or domain-specific guidance.

    Together, these elements make AgentContext the primary container responsible
    for assembling, formatting, and injecting all prompt-relevant context into
    LLM interactions.
    """  # noqa: E501

    skills: list[Skill] = Field(
        default_factory=list,
        description="List of available skills that can extend the user's input.",
    )
    system_message_suffix: str | None = Field(
        default=None, description="Optional suffix to append to the system prompt."
    )
    user_message_suffix: str | None = Field(
        default=None, description="Optional suffix to append to the user's message."
    )
    load_user_skills: bool = Field(
        default=False,
        description=(
            "Whether to automatically load user skills from ~/.openhands/skills/ "
            "and ~/.openhands/microagents/ (for backward compatibility). "
        ),
    )
    load_public_skills: bool = Field(
        default=False,
        description=(
            "Whether to automatically load skills from the public OpenHands "
            "skills repository at https://github.com/OpenHands/extensions. "
            "This allows you to get the latest skills without SDK updates."
        ),
    )
    marketplace_path: str | None = Field(
        default=DEFAULT_MARKETPLACE_PATH,
        description=(
            "Relative marketplace JSON path within the public skills repository. "
            "Set to None to load all public skills without marketplace filtering."
        ),
    )
    secrets: Mapping[str, SecretValue] | None = Field(
        default=None,
        description=(
            "Dictionary mapping secret keys to values or secret sources. "
            "Secrets are used for authentication and sensitive data handling. "
            "Values can be either strings or SecretSource instances "
            "(str | SecretSource)."
        ),
    )
    current_datetime: datetime | str | None = Field(
        default_factory=datetime.now,
        description=(
            "Current date and time information to provide to the agent. "
            "Can be a datetime object (which will be formatted as ISO 8601) "
            "or a pre-formatted string. When provided, this information is "
            "included in the system prompt to give the agent awareness of "
            "the current time context. Defaults to the current datetime."
        ),
    )

    @field_validator("skills")
    @classmethod
    def _validate_skills(cls, v: list[Skill], _info):
        if not v:
            return v
        # Check for duplicate skill names
        seen_names = set()
        for skill in v:
            if skill.name in seen_names:
                raise ValueError(f"Duplicate skill name found: {skill.name}")
            seen_names.add(skill.name)
        return v

    @model_validator(mode="after")
    def _load_auto_skills(self):
        """Load user and/or public skills if enabled."""
        if not self.load_user_skills and not self.load_public_skills:
            return self

        auto_skills = load_available_skills(
            work_dir=None,
            include_user=self.load_user_skills,
            include_project=False,
            include_public=self.load_public_skills,
            marketplace_path=self.marketplace_path,
        )

        existing_names = {skill.name for skill in self.skills}
        for name, skill in auto_skills.items():
            if name not in existing_names:
                self.skills.append(skill)
            else:
                logger.warning(
                    f"Skipping auto-loaded skill '{name}' (already in explicit skills)"
                )

        return self

    def get_secret_infos(self) -> list[dict[str, str | None]]:
        """Get secret information (name and description) from the secrets field.

        Returns:
            List of dictionaries with 'name' and 'description' keys.
            Returns an empty list if no secrets are configured.
            Description will be None if not available.
        """
        if not self.secrets:
            return []
        secret_infos: list[dict[str, str | None]] = []
        for name, secret_value in self.secrets.items():
            description = None
            if isinstance(secret_value, SecretSource):
                description = secret_value.description
            secret_infos.append({"name": name, "description": description})
        return secret_infos

    def get_formatted_datetime(self) -> str | None:
        """Get formatted datetime string for inclusion in prompts.

        Returns:
            Formatted datetime string, or None if current_datetime is not set.
            If current_datetime is a datetime object, it's formatted as ISO 8601.
            If current_datetime is already a string, it's returned as-is.
        """
        if self.current_datetime is None:
            return None
        if isinstance(self.current_datetime, datetime):
            return self.current_datetime.isoformat()
        return self.current_datetime

    def _partition_skills(self) -> tuple[list[Skill], list[Skill]]:
        """Split skills into repo-context and available-skills lists.

        Categorization rules (shared by system-message and ACP adapters):
        - AgentSkills-format: always in available_skills (progressive disclosure).
          Triggers also auto-inject via ``get_user_message_suffix``.
        - Legacy with ``trigger=None``: full content in REPO_CONTEXT (always active).
        - Legacy with triggers: listed in available_skills, injected on trigger.

        Returns:
            ``(repo_skills, available_skills)`` tuple.
        """
        repo_skills: list[Skill] = []
        available_skills: list[Skill] = []
        for s in self.skills:
            if s.is_agentskills_format or s.trigger is not None:
                available_skills.append(s)
            else:
                repo_skills.append(s)
        return repo_skills, available_skills

    def get_system_message_suffix(
        self,
        llm_model: str | None = None,
        llm_model_canonical: str | None = None,
        additional_secret_infos: list[dict[str, str | None]] | None = None,
    ) -> str | None:
        """Get the system message with repo skill content and custom suffix.

        Custom suffix can typically includes:
        - Repository information (repo name, branch name, PR number, etc.)
        - Runtime information (e.g., available hosts, current date)
        - Conversation instructions (e.g., user preferences, task details)
        - Repository-specific instructions (collected from repo skills)
        - Available skills list (for AgentSkills-format and triggered skills)

        Args:
            llm_model: Optional LLM model name for vendor-specific skill filtering.
            llm_model_canonical: Optional canonical LLM model name.
            additional_secret_infos: Optional list of additional secret info dicts
                (with 'name' and 'description' keys) to merge with agent_context
                secrets. Typically passed from conversation's secret_registry.

        Skill categorization:
        - AgentSkills-format (SKILL.md): Always in <available_skills> (progressive
          disclosure). If has triggers, content is ALSO auto-injected on trigger
          in user prompts.
        - Legacy with trigger=None: Full content in <REPO_CONTEXT> (always active)
        - Legacy with triggers: Listed in <available_skills>, injected on trigger
        """
        repo_skills, available_skills = self._partition_skills()

        # Gate vendor-specific repo skills based on model family.
        if llm_model or llm_model_canonical:
            spec = get_model_prompt_spec(llm_model or "", llm_model_canonical)
            family = (spec.family or "").lower()
            if family:
                filtered: list[Skill] = []
                for s in repo_skills:
                    n = (s.name or "").lower()
                    if n == "claude" and not (
                        "anthropic" in family or "claude" in family
                    ):
                        continue
                    if n == "gemini" and not (
                        "gemini" in family or "google_gemini" in family
                    ):
                        continue
                    filtered.append(s)
                repo_skills = filtered

        logger.debug(f"Loaded {len(repo_skills)} repository skills: {repo_skills}")

        # Generate available skills prompt
        available_skills_prompt = ""
        if available_skills:
            available_skills_prompt = to_prompt(available_skills)
            logger.debug(
                f"Generated available skills prompt for {len(available_skills)} skills"
            )

        # Build the workspace context information
        # Merge agent_context secrets with additional secrets from registry
        secret_infos = self.get_secret_infos()
        if additional_secret_infos:
            # Merge: additional secrets override agent_context secrets by name
            secret_dict = {s["name"]: s for s in secret_infos}
            for additional in additional_secret_infos:
                secret_dict[additional["name"]] = additional
            secret_infos = list(secret_dict.values())
        formatted_datetime = self.get_formatted_datetime()
        has_content = (
            repo_skills
            or self.system_message_suffix
            or secret_infos
            or available_skills_prompt
            or formatted_datetime
        )
        if has_content:
            formatted_text = render_template(
                prompt_dir=str(PROMPT_DIR),
                template_name="system_message_suffix.j2",
                repo_skills=repo_skills,
                system_message_suffix=self.system_message_suffix or "",
                secret_infos=secret_infos,
                available_skills_prompt=available_skills_prompt,
                current_datetime=formatted_datetime,
            ).strip()
            return formatted_text
        elif self.system_message_suffix and self.system_message_suffix.strip():
            return self.system_message_suffix.strip()
        return None

    _ACP_COMPATIBLE_FIELDS: frozenset[str] = frozenset(
        {
            "skills",
            "system_message_suffix",
            "user_message_suffix",
            "load_user_skills",
            "load_public_skills",
            "marketplace_path",
            "current_datetime",
        }
    )

    def validate_acp_compatibility(self) -> None:
        """Raise if this context uses fields unsupported by ACP prompt mode."""
        unsupported = set(self.model_fields_set) - self._ACP_COMPATIBLE_FIELDS
        if unsupported:
            fields = ", ".join(sorted(unsupported))
            raise NotImplementedError(
                f"ACP prompt context does not support AgentContext field(s): {fields}"
            )

    def to_acp_prompt_context(self) -> str | None:
        """Return the AgentContext fields that ACP can consume as prompt text.

        ACP servers own their tools, MCP servers, hooks, and execution model, so
        this adapter only emits prompt-only context. Unsupported AgentContext
        semantics must fail here instead of being silently approximated by
        ACPAgent.

        ``user_message_suffix`` is a compatible field but is not emitted here
        because ``LocalConversation`` already applies it through
        ``event.to_llm_message()``; including it would duplicate it.
        """
        self.validate_acp_compatibility()

        repo_skills, available_skills = self._partition_skills()

        has_explicit_prompt_context = (
            bool(self.skills)
            or bool(self.system_message_suffix)
            or bool(self.user_message_suffix)
            or "current_datetime" in self.model_fields_set
        )

        parts: list[str] = []
        if has_explicit_prompt_context:
            formatted_datetime = self.get_formatted_datetime()
            if formatted_datetime:
                parts.append(
                    "\n".join(
                        [
                            "<CURRENT_DATETIME>",
                            f"The current date and time is: {formatted_datetime}",
                            "</CURRENT_DATETIME>",
                        ]
                    )
                )

        if repo_skills:
            # Reuse the shared template only for REPO_CONTEXT rendering; ACP
            # prompt-only sections are assembled explicitly below.
            repo_context = render_template(
                prompt_dir=str(PROMPT_DIR),
                template_name="system_message_suffix.j2",
                repo_skills=repo_skills,
                system_message_suffix="",
                secret_infos=[],
                available_skills_prompt="",
                current_datetime=None,
            ).strip()
            if repo_context:
                parts.append(repo_context)

        if available_skills:
            parts.append(
                "\n".join(
                    [
                        "<SKILLS>",
                        "The following skills are available:",
                        to_prompt(available_skills),
                        "</SKILLS>",
                    ]
                )
            )

        if self.system_message_suffix:
            system_suffix = self.system_message_suffix.strip()
            if system_suffix:
                parts.append(
                    "\n".join(
                        [
                            "<SYSTEM_CONTEXT>",
                            system_suffix,
                            "</SYSTEM_CONTEXT>",
                        ]
                    )
                )

        if not parts:
            return None
        return "\n\n".join(parts)

    def get_user_message_suffix(
        self, user_message: Message, skip_skill_names: list[str]
    ) -> tuple[TextContent, list[str]] | None:
        """Augment the user’s message with knowledge recalled from skills.

        This works by:
        - Extracting the text content of the user message
        - Matching skill triggers against the query
        - Returning formatted knowledge and triggered skill names if relevant skills were triggered
        """  # noqa: E501

        user_message_suffix = None
        if self.user_message_suffix and self.user_message_suffix.strip():
            user_message_suffix = self.user_message_suffix.strip()

        query = "\n".join(
            c.text for c in user_message.content if isinstance(c, TextContent)
        ).strip()
        recalled_knowledge: list[SkillKnowledge] = []
        # skip empty queries, but still return user_message_suffix if it exists
        if not query:
            if user_message_suffix:
                return TextContent(text=user_message_suffix), []
            return None
        # Search for skill triggers in the query
        for skill in self.skills:
            if not isinstance(skill, Skill):
                continue
            trigger = skill.match_trigger(query)
            if trigger and skill.name not in skip_skill_names:
                logger.info(
                    "Skill '%s' triggered by keyword '%s'",
                    skill.name,
                    trigger,
                )
                recalled_knowledge.append(
                    SkillKnowledge(
                        name=skill.name,
                        trigger=trigger,
                        content=skill.content,
                        location=skill.source,
                    )
                )
        if recalled_knowledge:
            formatted_skill_text = render_template(
                prompt_dir=str(PROMPT_DIR),
                template_name="skill_knowledge_info.j2",
                triggered_agents=recalled_knowledge,
            )
            if user_message_suffix:
                formatted_skill_text += "\n" + user_message_suffix
            return TextContent(text=formatted_skill_text), [
                k.name for k in recalled_knowledge
            ]

        if user_message_suffix:
            return TextContent(text=user_message_suffix), []
        return None
