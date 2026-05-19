from __future__ import annotations

import pathlib
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from pydantic import (
    BaseModel,
    Field,
    PrivateAttr,
    SecretStr,
    field_serializer,
    field_validator,
    model_validator,
)

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
from openhands.sdk.utils.pydantic_secrets import serialize_secret


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
        default_factory=datetime.now,
        description=(
            "Current date and time information to provide to the agent. "
            "Can be a datetime object (which will be formatted as ISO 8601) "
            "or a pre-formatted string. When provided, this information is "
            "included in the system prompt to give the agent awareness of "
            "the current time context. Defaults to the current datetime."
        ),
        json_schema_extra={"acp_compatible": True},
    )

    # Snapshot of the skills that ``_load_auto_skills`` added on top of
    # the caller-supplied list. The serializer drops only the ones still
    # equal to this snapshot — if a downstream consumer replaces an
    # auto-loaded skill via ``model_copy(update={'skills': merged})``
    # (OpenHands' ``_create_agent_with_skills`` does this when it merges
    # in sandbox / repo skills with overlapping names), the replacement
    # has different field values and survives serialization. The same
    # auto-load will re-run on the receiving end and rebuild the
    # auto-loaded subset that wasn't replaced. For a stock configuration
    # that turns both flags on (~40 skills bundled under
    # ``~/.openhands/skills``) the resolved list is ~260 KB per
    # ``AgentContext`` — every ``GET`` on a stored conversation carried
    # that. See software-agent-sdk#3301.
    #
    # Values are deep-copied so an in-place caller mutation
    # (``ctx.skills[0].content = "custom"``) doesn't also mutate the
    # snapshot — without that, the equality check would still succeed
    # and the customised skill would silently disappear from the wire.
    _auto_loaded_skills: dict[str, Skill] = PrivateAttr(default_factory=dict)
    # The auto-load config that produced ``_auto_loaded_skills``.
    # Tracked so the serializer can no-op the trim when the config
    # changed after validation — e.g. ``model_copy(update={
    # "load_public_skills": False})``. Without this, a copy with the
    # flag flipped off would drop the auto-loaded skills on the wire
    # AND fail to re-load them on the next ``model_validate`` (the
    # flag is now off), losing them entirely. ``None`` when
    # ``_load_auto_skills`` has not run yet.
    _auto_load_config: tuple[bool, bool, str | None] | None = PrivateAttr(default=None)

    @field_serializer("skills", when_used="always", mode="wrap")
    def _serialize_skills(self, value: list[Skill], handler, info) -> Any:
        """Drop unmodified auto-loaded skills from the serialized output.

        The runtime keeps the full resolved list on ``self.skills`` so
        prompt rendering and downstream consumers behave exactly as
        today. Only the wire payload changes: callers re-loading the
        model will trigger ``_load_auto_skills`` again, which rebuilds
        the auto-loaded subset deterministically from the same
        ``load_user_skills`` / ``load_public_skills`` /
        ``marketplace_path`` configuration.

        Equality (not just name match) is required because consumers
        like OpenHands' ``_create_agent_with_skills`` replace
        auto-loaded skills in-place with their own version under the
        same name. A name-only filter would silently drop those
        replacements and the receiver would auto-reload the stock
        version on the next deserialization.

        Opt-out via ``context={"preserve_full_skills": True}``: paths
        that need a stable snapshot of the resolved skill catalog (the
        most important one being ``ConversationState._save_base_state``
        — persistence of the conversation to disk) pass this flag so
        the serializer is a no-op. Without it, a paused conversation
        resumed after the ``~/.openhands/skills`` directory or the
        public marketplace updated would silently pick up the *new*
        skill content via ``_load_auto_skills``. The API-response path
        skips the flag and gets the byte-size win.

        Config-drift safety: if the auto-load config
        (``load_user_skills`` / ``load_public_skills`` /
        ``marketplace_path``) changed since the snapshot was taken —
        typically via ``model_copy(update=...)`` flipping one of those
        flags — the trim is skipped. Otherwise the receiver would
        either re-load a *different* skill catalog (changed
        ``marketplace_path``) or fail to re-load at all (flag turned
        off), losing the auto-loaded skills entirely.

        ``mode="wrap"`` + ``handler`` delegation preserves caller
        options like ``exclude_none``, ``exclude_defaults``, and
        nested ``include`` / ``exclude`` for the surviving skills —
        manual ``s.model_dump(...)`` would have ignored them.
        """
        if info.context and info.context.get("preserve_full_skills"):
            return handler(value)
        # Auto-load config drifted (or never ran) → can't trust the
        # snapshot to round-trip. Serialize everything as explicit.
        current_config = (
            self.load_user_skills,
            self.load_public_skills,
            self.marketplace_path,
        )
        if self._auto_load_config != current_config:
            return handler(value)
        auto = self._auto_loaded_skills
        kept = [s for s in value if auto.get(s.name) != s]
        return handler(kept)

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
        """Load user and/or public skills if enabled.

        Names of skills added here are tracked in
        ``_auto_loaded_skill_names`` so the serializer can drop them
        from the wire payload (this validator re-runs on every model
        load, so the same skills repopulate without needing to be
        persisted).

        Migration: stored conversations created before the serializer
        change carry the resolved auto-loaded skill list inlined on
        ``skills``. When such a conversation is loaded back, the names
        match ``existing_names`` and the new-append branch is skipped
        — but we still mark them as auto-loaded if the persisted skill
        equals what the loader would produce now. Without this, every
        old conversation would keep the bloated payload until rewritten.
        A persisted skill that no longer matches the loader's current
        output (user edited the file, marketplace updated, etc.) stays
        treated as explicit so the on-disk content wins.
        """
        if not self.load_user_skills and not self.load_public_skills:
            return self

        auto_skills = load_available_skills(
            work_dir=None,
            include_user=self.load_user_skills,
            include_project=False,
            include_public=self.load_public_skills,
            marketplace_path=self.marketplace_path,
        )

        # Record the config that produced this snapshot so the
        # serializer can detect drift (e.g. ``model_copy(update={
        # "load_public_skills": False})``) and degrade to full
        # serialization.
        self._auto_load_config = (
            self.load_user_skills,
            self.load_public_skills,
            self.marketplace_path,
        )

        existing_by_name = {skill.name: skill for skill in self.skills}
        for name, skill in auto_skills.items():
            existing = existing_by_name.get(name)
            if existing is None:
                self.skills.append(skill)
                # Deep-copy so an in-place caller mutation
                # (``ctx.skills[0].content = "custom"``) doesn't also
                # mutate the snapshot — without it the equality check
                # would still succeed and the customised skill would
                # silently disappear from the wire.
                self._auto_loaded_skills[name] = skill.model_copy(deep=True)
            elif existing == skill:
                # Migration path for conversations stored before the
                # serializer change — see the docstring.
                self._auto_loaded_skills[name] = skill.model_copy(deep=True)
            else:
                logger.debug(
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

        The rendering reuses :meth:`get_system_message_suffix` with the same
        ``system_message_suffix.j2`` template so that ACP agents receive the
        identical prompt layout as the regular agent.  This includes the
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
        # renderer which also renders the <CUSTOM_SECRETS> block from secrets.
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
