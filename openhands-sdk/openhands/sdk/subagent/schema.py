"""Schema for Markdown-based agent definition files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal

import frontmatter
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from openhands.sdk.context.condenser import (
    CondenserBase,
    NoOpCondenser,
    default_condenser,
)
from openhands.sdk.context.condenser.llm_summarizing_condenser import (
    resolve_condenser_window,
)
from openhands.sdk.hooks.config import HookConfig
from openhands.sdk.mcp.config import MCPServer, coerce_mcp_config
from openhands.sdk.utils.path import to_posix_path


if TYPE_CHECKING:
    from openhands.sdk.llm import LLM
    from openhands.sdk.security.confirmation_policy import ConfirmationPolicyBase


# Scope where an agent was discovered. Set during discovery, not from frontmatter.
AgentDefinitionLevel = Literal["project", "user", "builtin", "plugin", "programmatic"]


KNOWN_FIELDS: Final[set[str]] = {
    "name",
    "description",
    "model",
    "color",
    "tools",
    "skills",
    "max_iteration_per_run",
    "max_budget_per_run",
    "hooks",
    "profile_store_dir",
    "mcp_config",
    "mcp_servers",
    "permission_mode",
    "condenser",
}

_VALID_PERMISSION_MODES: Final[set[str]] = {
    "always_confirm",
    "never_confirm",
    "confirm_risky",
}


def _extract_color(fm: dict[str, object]) -> str | None:
    """Extract color from frontmatter."""
    color_raw = fm.get("color")
    color: str | None = str(color_raw) if color_raw is not None else None
    return color


def _extract_tools(fm: dict[str, object]) -> list[str]:
    """Extract tools from frontmatter."""
    tools_raw = fm.get("tools", [])

    # Ensure tools is a list of strings
    tools: list[str]
    if isinstance(tools_raw, str):
        tools = [tools_raw]
    elif isinstance(tools_raw, list):
        tools = [str(t) for t in tools_raw]
    else:
        tools = []
    return tools


def _extract_skills(fm: dict[str, object]) -> list[str]:
    """Extract skill names from frontmatter."""
    skills_raw = fm.get("skills", [])
    skills: list[str]
    if isinstance(skills_raw, str):
        skills = [s.strip() for s in skills_raw.split(",") if s.strip()]
    elif isinstance(skills_raw, list):
        skills = [str(s) for s in skills_raw]
    else:
        skills = []
    return skills


def _extract_mcp_config(fm: dict[str, object]) -> dict[str, MCPServer] | None:
    """Extract MCP servers from frontmatter.

    Variable placeholders (``${VAR}`` and ``${VAR:-default}``) are preserved
    and expanded later when the agent runs, allowing per-conversation secrets
    to be injected at runtime. Expansion happens in LocalConversation when
    the agent's mcp_config are processed.

    Note: The older ``$VAR`` syntax (without braces) is NOT supported.
    Use ``${VAR}`` for environment variables and secrets.
    """
    mcp_config_raw = fm.get("mcp_config", fm.get("mcp_servers"))
    if mcp_config_raw is None:
        return None
    if not isinstance(mcp_config_raw, dict):
        raise ValueError(
            f"mcp_config must be a mapping of server names to configs, "
            f"got {type(mcp_config_raw)}"
        )
    return coerce_mcp_config(mcp_config_raw)


def _extract_profile_store_dir(fm: dict[str, object]) -> str | None:
    """Extract profile store directory from frontmatter."""
    profile_store_dir_raw = fm.get("profile_store_dir")
    if profile_store_dir_raw is None:
        return None
    if isinstance(profile_store_dir_raw, str):
        return profile_store_dir_raw
    raise ValueError(
        f"profile_store_dir must be a scalar value, got {type(profile_store_dir_raw)}"
    )


def _extract_examples(description: str) -> list[str]:
    """Extract <example> tags from description for agent triggering."""
    pattern = r"<example>(.*?)</example>"
    matches = re.findall(pattern, description, re.DOTALL | re.IGNORECASE)
    return [m.strip() for m in matches if m.strip()]


def _extract_permission_mode(fm: dict[str, object]) -> str | None:
    """Extract permission_mode from frontmatter, defaulting to None (inherit parent)."""
    raw = fm.get("permission_mode")
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if value not in _VALID_PERMISSION_MODES:
        raise ValueError(
            f"Invalid permission_mode '{raw}'. "
            f"Must be one of: {', '.join(sorted(_VALID_PERMISSION_MODES))}"
        )
    return value


def _extract_max_iteration_per_run(fm: dict[str, object]) -> int | None:
    """Extract max iterations per run from frontmatter file."""
    max_iter_raw = fm.get("max_iteration_per_run")
    # YAML reads an unquoted yes/no/on/off as a bool, and bool is an int in
    # Python; treating it as a count would silently cap the run at one.
    if isinstance(max_iter_raw, bool):
        return None
    if isinstance(max_iter_raw, str):
        return int(max_iter_raw)
    if isinstance(max_iter_raw, int):
        return max_iter_raw
    return None


def _extract_max_budget_per_run(fm: dict[str, object]) -> float | None:
    """Extract the per-run cost budget (USD) from a frontmatter file."""
    raw = fm.get("max_budget_per_run")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        return float(raw)
    return None


def _extract_hooks(fm: dict[str, object]) -> HookConfig | None:
    # Parse hooks configuration
    hooks_raw = fm.get("hooks")
    hooks: HookConfig | None = None
    if hooks_raw is not None and isinstance(hooks_raw, dict):
        hooks = HookConfig.model_validate(hooks_raw)
    return hooks


class AgentDefinitionError(ValueError):
    """An invalid agent definition, naming the file and offending field."""

    def __init__(
        self,
        message: str,
        *,
        name: str | None = None,
        source: str | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.name = name
        self.source = source
        self.field = field


class SubAgentCondenserSettings(BaseModel):
    """Tuning parameters for a sub-agent's LLM summarizing condenser.

    Frontmatter stores tuning only; unset fields keep the defaults applied by
    :func:`default_condenser`, and the condenser LLM is injected at spawn.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["llm_summarizing"] = "llm_summarizing"
    enabled: bool = True
    max_size: int | None = Field(default=None, gt=0)
    max_tokens: int | None = Field(default=None, gt=0)
    keep_first: int | None = Field(default=None, ge=0, validate_default=True)

    @field_validator("keep_first")
    @classmethod
    def _validate_window(
        cls, keep_first: int | None, info: ValidationInfo
    ) -> int | None:
        # Field-level so the error names keep_first; max_size is declared first so
        # info.data has it, and validate_default also checks an omitted keep_first.
        if info.data.get("enabled", True):
            window, resolved = resolve_condenser_window(
                info.data.get("max_size"), keep_first
            )
            if window // 2 - resolved - 1 <= 0:
                raise ValueError(
                    "keep_first must be less than max_size // 2 to leave room for "
                    "condensation"
                )
        return keep_first

    def build_condenser(self, llm: LLM) -> CondenserBase:
        """Build the condenser, injecting the agent's LLM at factory time."""
        if not self.enabled:
            return NoOpCondenser()
        return default_condenser(
            llm.model_copy(update={"usage_id": "condenser"}),
            max_size=self.max_size,
            keep_first=self.keep_first,
            max_tokens=self.max_tokens,
        )


# Kinds that select the summarizing condenser whose tuning params we accept.
# Any other ``kind`` is treated as a full condenser spec (e.g. NoOpCondenser).
_SUMMARIZING_CONDENSER_KINDS: Final[set[str]] = {
    "llm_summarizing",
    "LLMSummarizingCondenser",
}


def _condenser_validation_error(
    exc: ValidationError, *, name: str, source: str
) -> AgentDefinitionError:
    first = exc.errors()[0]
    field = ".".join(str(part) for part in first["loc"]) or "condenser"
    return AgentDefinitionError(
        f"Invalid condenser in {source} (agent '{name}'): field '{field}': "
        f"{first['msg']}",
        name=name,
        source=source,
        field=field,
    )


def _extract_condenser(
    fm: dict[str, Any], *, name: str, source: str
) -> CondenserBase | SubAgentCondenserSettings | None:
    """Extract the condenser config from frontmatter.

    None / 'default' -> None; 'none'/'off'/false -> NoOpCondenser; a mapping
    without ``llm`` tunes the summarizing condenser; any other mapping is a full
    condenser spec.
    """
    raw = fm.get("condenser")
    if raw is None or isinstance(raw, (CondenserBase, SubAgentCondenserSettings)):
        return raw
    if isinstance(raw, bool):
        return None if raw else NoOpCondenser()
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in ("", "default", "inherit"):
            return None
        if value in ("none", "off", "false", "no", "disable", "disabled"):
            return NoOpCondenser()
        raise AgentDefinitionError(
            f"Invalid condenser value {raw!r} in {source} (agent '{name}'). "
            "Use 'default', 'none', or a mapping of condenser tuning parameters.",
            name=name,
            source=source,
            field="condenser",
        )
    if isinstance(raw, dict):
        kind = raw.get("kind")
        if kind is not None and (
            kind not in _SUMMARIZING_CONDENSER_KINDS or "llm" in raw
        ):
            # Full spec (e.g. NoOpCondenser, or a legacy inline-llm mapping);
            # normalize our settings spelling to the real condenser kind.
            spec = (
                {**raw, "kind": "LLMSummarizingCondenser"}
                if kind == "llm_summarizing"
                else raw
            )
            try:
                return CondenserBase.model_validate(spec)
            except ValidationError as e:
                raise _condenser_validation_error(e, name=name, source=source) from e
        tuning = {k: v for k, v in raw.items() if k != "kind"}
        try:
            return SubAgentCondenserSettings.model_validate(tuning)
        except ValidationError as e:
            raise _condenser_validation_error(e, name=name, source=source) from e
    raise AgentDefinitionError(
        f"Invalid condenser value {raw!r} in {source} (agent '{name}').",
        name=name,
        source=source,
        field="condenser",
    )


class AgentDefinition(BaseModel):
    """Agent definition loaded from Markdown file.

    Agents are specialized configurations that can be triggered based on
    user input patterns. They define custom system prompts and tool access.
    """

    name: str = Field(description="Agent name (from frontmatter or filename)")
    description: str = Field(default="", description="Agent description")
    model: str = Field(
        default="inherit", description="Model to use ('inherit' uses parent model)"
    )
    color: str | None = Field(default=None, description="Display color for the agent")
    tools: list[str] = Field(
        default_factory=list, description="List of allowed tools for this agent"
    )
    skills: list[str] = Field(
        default_factory=list,
        description="List of skill names for this agent. "
        "Resolved from project/user directories.",
    )
    system_prompt: str = Field(default="", description="System prompt content")
    source: str | None = Field(
        default=None, description="Source file path for this agent"
    )
    level: AgentDefinitionLevel | None = Field(
        default=None,
        description="Scope where the agent was discovered. "
        "Set during discovery; None when loaded directly from a file.",
    )
    when_to_use_examples: list[str] = Field(
        default_factory=list,
        description="Examples of when to use this agent (for triggering)",
    )
    hooks: HookConfig | None = Field(
        default=None, description="Hook configuration for this agent"
    )
    permission_mode: str | None = Field(
        default=None,
        description="How the subagent handles permissions. "
        "None inherits the parent policy, 'always_confirm' requires "
        "confirmation for every action, 'never_confirm' skips all confirmations, "
        "'confirm_risky' only confirms actions above a risk threshold.",
    )
    max_iteration_per_run: int | None = Field(
        default=None,
        description="Maximum iterations per run. "
        "It must be strictly positive, or None for default.",
        gt=0,
    )
    max_budget_per_run: float | None = Field(
        default=None,
        description="Maximum accumulated cost (USD) per run for this sub-agent. "
        "Must be strictly positive, or None for no budget.",
        gt=0,
    )
    mcp_config: dict[str, MCPServer] | None = Field(
        default=None,
        description=(
            "MCP servers for this agent. Ignored when it runs as a delegated "
            "sub-agent, which always inherits its parent's MCP servers."
        ),
        examples=[
            {
                "fetch": {
                    "command": "uvx",
                    "args": [
                        "--with",
                        "mcp==1.29.0",
                        "mcp-server-fetch==2026.7.10",
                    ],
                }
            }
        ],
    )
    profile_store_dir: str | None = Field(
        default=None,
        description="Path to the directory where LLM profiles are stored. "
        "If None, the default profile store directory is used.",
    )
    condenser: CondenserBase | SubAgentCondenserSettings | None = Field(
        default=None,
        description=(
            "Context condenser for the sub-agent. None applies a default "
            "summarizing condenser; SubAgentCondenserSettings holds frontmatter "
            "tuning; a NoOpCondenser disables condensation."
        ),
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata from frontmatter"
    )

    def get_confirmation_policy(self) -> ConfirmationPolicyBase | None:
        """Convert permission_mode to a ConfirmationPolicyBase instance.

        Returns None when permission_mode is None (inherit parent policy).
        """
        if self.permission_mode is None:
            return None

        match self.permission_mode:
            case "always_confirm":
                from openhands.sdk.security.confirmation_policy import AlwaysConfirm

                return AlwaysConfirm()
            case "never_confirm":
                from openhands.sdk.security.confirmation_policy import NeverConfirm

                return NeverConfirm()
            case "confirm_risky":
                from openhands.sdk.security.confirmation_policy import ConfirmRisky

                return ConfirmRisky()
            case _:
                # Should never reach here due to validation
                # in _extract_permission_mode()
                raise AssertionError(
                    f"Unexpected permission_mode: {self.permission_mode}"
                )

    @classmethod
    def load(cls, agent_path: Path) -> AgentDefinition:
        """Load an agent definition from a Markdown file.

        Agent Markdown files have YAML frontmatter with:
        - name: Agent name
        - description: Description with optional <example> tags for triggering
        - tools (optional): List of allowed tools
        - skills (optional): Comma-separated skill names or list of skill names
        - mcp_config (optional): MCP server configurations mapping
        - model (optional): Model profile to use (default: 'inherit')
        - color (optional): Display color
        - permission_mode (optional): How the subagent handles permissions
          ('always_confirm', 'never_confirm', 'confirm_risky'). None inherits parent.
        - max_iterations_per_run: Max iteration per run
        - condenser (optional): `none`/`false` disables condensation; a mapping
          sets LLM-summarizing tuning (max_size, keep_first, max_tokens, enabled).
        - hooks (optional): List of applicable hooks

        The body of the Markdown is the system prompt.

        Args:
            agent_path: Path to the agent Markdown file.

        Returns:
            Loaded AgentDefinition instance.
        """
        with open(agent_path, encoding="utf-8-sig") as f:
            post = frontmatter.load(f)

        fm = post.metadata
        content = post.content.strip()

        # Extract frontmatter fields with proper type handling
        name: str = str(fm.get("name") or agent_path.stem)
        source = to_posix_path(agent_path)
        description: str = str(fm.get("description") or "")
        model: str = str(fm.get("model") or "inherit")
        color: str | None = _extract_color(fm)
        tools: list[str] = _extract_tools(fm)
        skills: list[str] = _extract_skills(fm)
        permission_mode: str | None = _extract_permission_mode(fm)
        max_iteration_per_run: int | None = _extract_max_iteration_per_run(fm)
        max_budget_per_run: float | None = _extract_max_budget_per_run(fm)
        mcp_config: dict[str, MCPServer] | None = _extract_mcp_config(fm)
        profile_store_dir: str | None = _extract_profile_store_dir(fm)
        hooks: HookConfig | None = _extract_hooks(fm)
        condenser: CondenserBase | SubAgentCondenserSettings | None = (
            _extract_condenser(fm, name=name, source=source)
        )

        # Extract whenToUse examples from description
        when_to_use_examples = _extract_examples(description)

        # Remove known fields from metadata to get extras
        metadata = {k: v for k, v in fm.items() if k not in KNOWN_FIELDS}

        return cls(
            name=name,
            description=description,
            model=model,
            color=color,
            tools=tools,
            skills=skills,
            permission_mode=permission_mode,
            max_iteration_per_run=max_iteration_per_run,
            max_budget_per_run=max_budget_per_run,
            mcp_config=mcp_config,
            hooks=hooks,
            profile_store_dir=profile_store_dir,
            condenser=condenser,
            system_prompt=content,
            source=source,
            when_to_use_examples=when_to_use_examples,
            metadata=metadata,
        )
