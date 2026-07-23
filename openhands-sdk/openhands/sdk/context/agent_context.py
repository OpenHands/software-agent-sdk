from __future__ import annotations

import pathlib
from collections.abc import Mapping
from datetime import datetime
from typing import Any, NamedTuple

from pydantic import (
    BaseModel,
    Field,
    SecretStr,
    ValidationInfo,
    field_serializer,
    field_validator,
    model_validator,
)

from openhands.sdk.context.prompts import render_template
from openhands.sdk.context.prompts.presets import create_registry
from openhands.sdk.context.prompts.section import PromptContext
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.llm.utils.model_prompt_spec import get_model_prompt_spec
from openhands.sdk.logger import get_logger
from openhands.sdk.marketplace.registration import MarketplaceRegistration
from openhands.sdk.secret import SecretSource, SecretValue
from openhands.sdk.skills import (
    Skill,
    SkillKnowledge,
    load_available_skills,
    merge_skills_by_name,
    to_prompt,
)
from openhands.sdk.skills.skill import DEFAULT_MARKETPLACE_PATH
from openhands.sdk.utils.pydantic_secrets import (
    serialize_secret,
    validate_secret_dict,
)


logger = get_logger(__name__)

PROMPT_DIR = pathlib.Path(__file__).parent / "prompts" / "templates"


class ResolvedDynamicData(NamedTuple):
    """Dynamic-tier inputs resolved once, fed into the section registry
    (skills gated by model family, secrets merged)."""

    repo_skills: list[Skill]
    available_skills_prompt: str
    secret_infos: list[dict[str, str | None]]
    formatted_datetime: str | None


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
        json_schema_extra={"acp_compatible": True},
    )
    system_message_suffix: str | None = Field(
        default=None,
        description="Optional suffix to append to the system prompt.",
        json_schema_extra={"acp_compatible": True},
    )
    user_message_suffix: str | None = Field(
        default=None,
        description="Optional suffix to append to the user's message.",
        json_schema_extra={"acp_compatible": True},
    )
    load_user_skills: bool = Field(
        default=False,
        description=(
            "Whether to automatically load user skills from ~/.openhands/skills/ "
            "and ~/.openhands/microagents/ (for backward compatibility). "
        ),
        json_schema_extra={"acp_compatible": True},
    )
    load_public_skills: bool = Field(
        default=False,
        description=(
            "Whether to automatically load skills from the public OpenHands "
            "skills repository at https://github.com/OpenHands/extensions. "
            "This allows you to get the latest skills without SDK updates."
        ),
        json_schema_extra={"acp_compatible": True},
    )
    marketplace_path: str | None = Field(
        default=DEFAULT_MARKETPLACE_PATH,
        description=(
            "Relative marketplace JSON path within the public skills repository. "
            "Set to None to load all public skills without marketplace filtering."
        ),
        json_schema_extra={"acp_compatible": True},
    )
    registered_marketplaces: list[MarketplaceRegistration] = Field(
        default_factory=list,
        description=(
            "Marketplace registrations for plugin resolution. Registrations with "
            "auto_load=True or a list of plugin names are resolved by "
            "LocalConversation at startup."
        ),
        json_schema_extra={"acp_compatible": True},
    )
    load_project_skills: bool = Field(
        default=False,
        description=(
            "Whether to automatically load project skills from the conversation "
            "workspace (e.g. .openhands/skills/, AGENTS.md). Unlike "
            "load_user_skills / load_public_skills, this flag is not resolved by "
            "AgentContext itself (the workspace path is unknown at validation "
            "time); LocalConversation resolves it lazily on the first "
            "send_message() / run(), when the workspace is known. Also unlike "
            "load_user_skills / load_public_skills (which yield to explicit "
            "skills on a name conflict), resolved project skills are "
            "authoritative: a project skill overrides a same-named skill already "
            "present in `skills`."
        ),
        json_schema_extra={"acp_compatible": True},
    )
    load_memory: bool = Field(
        default=False,
        description=(
            "Whether to load persistent agent memory (MEMORY.md indexes under "
            "~/.openhands/memory/ and <workspace>/.openhands/memory/) into the "
            "system prompt. Like load_project_skills, this flag is not "
            "resolved by AgentContext itself (the workspace path is unknown "
            "at validation time); LocalConversation resolves it lazily on the "
            "first send_message() / run() and stores the result in "
            "memory_context."
        ),
        json_schema_extra={"acp_compatible": True},
    )
    memory_context: str | None = Field(
        default=None,
        exclude=True,
        description=(
            "Resolved memory-index text rendered into the <MEMORY_CONTEXT> "
            "prompt block. Populated via model_copy by LocalConversation when "
            "load_memory is set; excluded from serialization because it is "
            "re-resolved from disk each session and must not bloat persisted "
            "conversation state."
        ),
        json_schema_extra={"acp_compatible": True},
    )
    disabled_skills: list[str] = Field(
        default_factory=list,
        description=(
            "Names of skills to EXCLUDE from this context — a deny-list applied "
            "after every skill source is loaded (auto-loaded user/public, "
            "explicit, and lazily-loaded project skills). A listed name absent "
            "from the loaded set is a harmless no-op. [] (the default) keeps "
            "every skill. This is the single, drift-tolerant skill-selection "
            "mechanism (agent profiles set it from their own deny-list; #4017)."
        ),
        json_schema_extra={"acp_compatible": True},
    )
    secrets: Mapping[str, SecretValue] | None = Field(
        default=None,
        description=(
            "Dictionary mapping secret keys to values or secret sources. "
            "Secrets are used for authentication and sensitive data handling. "
            "Values can be either strings or SecretSource instances "
            "(str | SecretSource)."
        ),
        json_schema_extra={"acp_compatible": True},
    )
    current_datetime: datetime | str | None = Field(
        # Timezone-aware local "now"; get_formatted_datetime renders it to the
        # minute for the prompt.
        default_factory=lambda: datetime.now().astimezone(),
        description=(
            "Current date and time information to provide to the agent. "
            "Can be a datetime object (which will be formatted as ISO 8601) "
            "or a pre-formatted string. When provided, this information is "
            "included in the system prompt to give the agent awareness of "
            "the current time context. Defaults to the current "
            "(timezone-aware) datetime."
        ),
        json_schema_extra={"acp_compatible": True},
    )

    @field_validator("secrets", mode="before")
    @classmethod
    def _decrypt_secrets(cls, value: Any, info: ValidationInfo) -> Any:
        """Decrypt persisted raw-string ``secrets`` values when a cipher
        is in context.

        ``_serialize_secrets`` writes each raw-string value through
        :func:`serialize_secret`, which produces Fernet ciphertext under
        cipher context. Without a matching ``mode='before'`` decryption
        validator, that ciphertext would survive round-trips through
        :class:`StartConversationRequest` (whose
        ``_populate_agent_from_settings`` validator runs *without*
        cipher context) and get injected into the agent's system prompt
        as-is — same bug class as any secret-bearing dict field that
        round-trips without a matching decryption validator (e.g. MCP
        ``env`` / ``headers``).

        ``SecretSource`` entries are dict-shaped on the wire (Pydantic
        models), so they're skipped by :func:`validate_secret_dict`'s
        ``isinstance(value, str)`` gate and continue to construct
        normally through their own validators.
        """
        return validate_secret_dict(value, info, description="AgentContext secrets")

    @field_serializer("secrets", when_used="always")
    def _serialize_secrets(
        self, value: Mapping[str, SecretValue] | None, info
    ) -> dict[str, Any] | None:
        """Mask raw-string ``secrets`` values via :func:`serialize_secret`."""
        if value is None:
            return None
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(v, SecretSource):
                out[k] = v.model_dump(mode=info.mode, context=info.context)
            else:
                out[k] = serialize_secret(SecretStr(v), info)
        return out

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
        """Load user/legacy-public skills if enabled, then apply ``disabled_skills``."""
        include_public = self.load_public_skills
        if self.load_user_skills or include_public:
            auto_skills = load_available_skills(
                work_dir=None,
                include_user=self.load_user_skills,
                include_project=False,
                include_public=include_public,
                marketplace_path=self.marketplace_path,
            )

            # Explicit skills are authoritative; auto-loaded skills only fill gaps.
            explicit_names = {skill.name for skill in self.skills}
            for name in auto_skills:
                if name in explicit_names:
                    logger.debug(
                        f"Skipping auto-loaded skill '{name}' "
                        "(already in explicit skills)"
                    )
            self.skills = merge_skills_by_name(self.skills, auto_skills.values())

        # Deny-list: drop disabled skills from whatever ended up loaded (explicit
        # + auto). A disabled name that isn't present is a harmless no-op — this
        # is the drift-tolerant selection model that replaced allow-list refs.
        if self.disabled_skills:
            disabled = set(self.disabled_skills)
            self.skills = [s for s in self.skills if s.name not in disabled]
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
            If current_datetime is a datetime object, it's formatted as
            "YYYY-MM-DDTHH:MM" (no seconds, microseconds, or UTC offset).
            If current_datetime is already a string, it's returned as-is.
        """
        if self.current_datetime is None:
            return None
        if isinstance(self.current_datetime, datetime):
            # Local wall-clock to the minute: drop seconds, microseconds, offset.
            return self.current_datetime.replace(tzinfo=None).isoformat(
                timespec="minutes"
            )
        return self.current_datetime

    def _partition_skills(self) -> tuple[list[Skill], list[Skill]]:
        """Split skills into repo-context and available-skills lists.

        Categorization rules (shared by system-message and ACP adapters):
        - AgentSkills-format: available_skills unless direct model invocation is
          disabled. Triggers still auto-inject via ``get_user_message_suffix``.
        - Legacy with ``trigger=None``: full content in REPO_CONTEXT (always active).
        - Legacy with triggers: listed in available_skills unless direct model
          invocation is disabled, injected on trigger.

        Returns:
            ``(repo_skills, available_skills)`` tuple.
        """
        repo_skills: list[Skill] = []
        available_skills: list[Skill] = []
        for s in self.skills:
            if s.is_agentskills_format or s.trigger is not None:
                # Path rules force disable_model_invocation (Skill validator), so
                # they fall out here — injected only on file-touch, never listed.
                if not s.disable_model_invocation:
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
        data = self._resolve_dynamic_data(
            llm_model, llm_model_canonical, additional_secret_infos
        )
        ctx = PromptContext(
            now=data.formatted_datetime,
            repo_skills=tuple((s.name, s.content) for s in data.repo_skills),
            available_skills_prompt=data.available_skills_prompt or None,
            custom_suffix=self.system_message_suffix or None,
            memory_context=self.memory_context or None,
            secret_infos=tuple(
                (info["name"] or "", info["description"]) for info in data.secret_infos
            ),
        )
        return create_registry().build(ctx).dynamic

    def _resolve_dynamic_data(
        self,
        llm_model: str | None = None,
        llm_model_canonical: str | None = None,
        additional_secret_infos: list[dict[str, str | None]] | None = None,
    ) -> ResolvedDynamicData:
        """Resolve the dynamic-tier inputs shared by :meth:`get_system_message_suffix`
        and the section registry: model-gated repo skills, the available-skills
        prompt, merged secret infos, and the formatted datetime. Pure (no render)."""
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

        available_skills_prompt = ""
        if available_skills:
            available_skills_prompt = to_prompt(available_skills)
            logger.debug(
                f"Generated available skills prompt for {len(available_skills)} skills"
            )

        # Merge agent_context secrets with additional secrets from the registry
        # (additional override by name).
        secret_infos = self.get_secret_infos()
        if additional_secret_infos:
            secret_dict = {s["name"]: s for s in secret_infos}
            for additional in additional_secret_infos:
                secret_dict[additional["name"]] = additional
            secret_infos = list(secret_dict.values())

        return ResolvedDynamicData(
            repo_skills=repo_skills,
            available_skills_prompt=available_skills_prompt,
            secret_infos=secret_infos,
            formatted_datetime=self.get_formatted_datetime(),
        )

    def validate_acp_compatibility(self) -> None:
        """Raise if this context uses fields unsupported by ACP prompt mode.

        Compatibility is determined by the ``acp_compatible`` tag in each
        field's ``json_schema_extra``.
        """
        acp_compatible = {
            name
            for name, info in type(self).model_fields.items()
            if isinstance(info.json_schema_extra, dict)
            and info.json_schema_extra.get("acp_compatible") is True
        }
        unsupported = set(self.model_fields_set) - acp_compatible
        if unsupported:
            fields = ", ".join(sorted(unsupported))
            raise NotImplementedError(
                f"ACP prompt context does not support AgentContext field(s): {fields}"
            )

    def to_acp_prompt_context(
        self,
        additional_secret_infos: list[dict[str, str | None]] | None = None,
    ) -> str | None:
        """Return the AgentContext fields that ACP can consume as prompt text.

        ACP servers own their tools, MCP servers, hooks, and execution model, so
        this adapter only emits prompt-only context.  Unsupported AgentContext
        fields are rejected by :meth:`validate_acp_compatibility`.

        The rendering reuses :meth:`get_system_message_suffix`, which assembles
        the dynamic-tier sections via the shared prompt registry, so that ACP
        agents receive the identical prompt layout as the regular agent.  This
        includes the
        ``<CUSTOM_SECRETS>`` block when secrets are present, informing the ACP
        subprocess which environment variables are available.  The actual secret
        values are injected into the subprocess environment by
        ``ACPAgent._start_acp_server``; the prompt block only advertises their
        names so the agent knows to use them.

        ``user_message_suffix`` is a compatible field but is not emitted here
        because ``LocalConversation`` already applies it through
        ``event.to_llm_message()``; including it would duplicate it.

        Args:
            additional_secret_infos: Optional list of additional secret info dicts
                from the conversation's secret_registry, matching the interface of
                :meth:`get_system_message_suffix`. When provided, these secrets are
                merged with any secrets already on the AgentContext so the rendered
                ``<CUSTOM_SECRETS>`` block matches what the regular Agent emits.
        """
        self.validate_acp_compatibility()
        # No model-specific skill filtering for ACP — delegate to the shared
        # builder, whose dynamic-tier sections also emit the <CUSTOM_SECRETS>
        # block from secrets.
        return self.get_system_message_suffix(
            additional_secret_infos=additional_secret_infos
        )

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

    def get_tool_use_suffix(
        self, file_path: str, skip_skill_names: list[str]
    ) -> tuple[TextContent, list[str]] | None:
        """Match ``PathTrigger`` skills ("rules") against a touched file path.

        The path-based counterpart of :meth:`get_user_message_suffix`. Returns
        the rendered rule content and the rules that fired, or None.
        ``skip_skill_names`` suppresses rules already injected (dedup).
        """
        if not file_path:
            return None

        recalled_knowledge: list[SkillKnowledge] = []
        for skill in self.skills:
            if not isinstance(skill, Skill) or skill.name in skip_skill_names:
                continue
            if pattern := skill.match_path_trigger(file_path):
                logger.info(
                    "Rule '%s' triggered by path match '%s' for '%s'",
                    skill.name,
                    pattern,
                    file_path,
                )
                recalled_knowledge.append(
                    SkillKnowledge(
                        name=skill.name,
                        trigger=pattern,
                        content=skill.content,
                        location=skill.source,
                    )
                )

        if not recalled_knowledge:
            return None

        blocks = [
            "<EXTRA_INFO>\n"
            "The following rule applies because a file you touched matches "
            f'"{k.trigger}". Follow it when working with matching files.\n'
            + (f"Rule location: {k.location}\n" if k.location else "")
            + f"\n{k.content}\n</EXTRA_INFO>"
            for k in recalled_knowledge
        ]
        return TextContent(text="\n".join(blocks)), [k.name for k in recalled_knowledge]
