"""Schema for Markdown-based agent definition files."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import frontmatter
from pydantic import BaseModel, Field

from openhands.sdk.hooks.config import HookConfig


if TYPE_CHECKING:
    from openhands.sdk.security.confirmation_policy import ConfirmationPolicyBase


KNOWN_FIELDS: Final[set[str]] = {
    "name",
    "description",
    "model",
    "color",
    "tools",
    "skills",
    "max_iteration_per_run",
    "hooks",
    "profile_store_dir",
    "mcp_servers",
    "permission_mode",
}

_VALID_PERMISSION_MODES: Final[set[str]] = {
    "always_confirm",
    "never_confirm",
    "confirm_risky",
}


def _resolve_env_vars(value: str) -> str:
    """Expand environment variable references in *value* using ``os.path.expandvars``.

    Supports ``$VAR`` and ``${VAR}`` syntax.  If a referenced variable is not
    set, the placeholder is left unchanged (standard ``expandvars`` behaviour).

    Args:
        value: A string potentially containing environment variable references.

    Returns:
        The string with all recognised environment variables expanded.
    """
    return os.path.expandvars(value)


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


def _extract_mcp_servers(fm: dict[str, Any]) -> dict[str, Any] | None:
    """Extract MCP servers configuration from frontmatter.

    Note that environment variable references of the form `${VAR}` inside any
    string value of each server config are resolved from `os.environ`
    at parse time so that Markdown-based definitions can forward secrets
    without hard-coding them.
    """
    mcp_servers_raw = fm.get("mcp_servers")
    if mcp_servers_raw is None:
        return None
    if not isinstance(mcp_servers_raw, dict):
        raise ValueError(
            f"mcp_servers must be a mapping of server names to configs, "
            f"got {type(mcp_servers_raw)}"
        )

    # Resolve ${VAR} / $VAR references in all string values
    def _resolve_deep(value: Any) -> Any:
        if isinstance(value, str):
            return _resolve_env_vars(value)
        if isinstance(value, dict):
            return {k: _resolve_deep(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_resolve_deep(item) for item in value]
        return value

    for server_name, server_cfg in mcp_servers_raw.items():
        if isinstance(server_cfg, dict):
            mcp_servers_raw[server_name] = _resolve_deep(server_cfg)
    return mcp_servers_raw


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
    if isinstance(max_iter_raw, str):
        return int(max_iter_raw)
    if isinstance(max_iter_raw, int):
        return max_iter_raw
    return None


def _extract_hooks(fm: dict[str, object]) -> HookConfig | None:
    # Parse hooks configuration
    hooks_raw = fm.get("hooks")
    hooks: HookConfig | None = None
    if hooks_raw is not None and isinstance(hooks_raw, dict):
        hooks = HookConfig.model_validate(hooks_raw)
    return hooks


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
    mcp_servers: dict[str, Any] | None = Field(
        default=None,
        description="MCP server configurations for this agent. "
        "Keys are server names, values are server configs with 'command', 'args', etc.",
        examples=[{"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}],
    )
    profile_store_dir: str | None = Field(
        default=None,
        description="Path to the directory where LLM profiles are stored. "
        "If None, the default profile store directory is used.",
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
        - mcp_servers (optional): MCP server configurations mapping
        - model (optional): Model profile to use (default: 'inherit')
        - color (optional): Display color
        - permission_mode (optional): How the subagent handles permissions
          ('always_confirm', 'never_confirm', 'confirm_risky'). None inherits parent.
        - max_iterations_per_run: Max iteration per run
        - hooks (optional): List of applicable hooks

        The body of the Markdown is the system prompt.

        Args:
            agent_path: Path to the agent Markdown file.

        Returns:
            Loaded AgentDefinition instance.
        """
        with open(agent_path) as f:
            post = frontmatter.load(f)

        fm = post.metadata
        content = post.content.strip()

        # Extract frontmatter fields with proper type handling
        name: str = str(fm.get("name", agent_path.stem))
        description: str = str(fm.get("description", ""))
        model: str = str(fm.get("model", "inherit"))
        color: str | None = _extract_color(fm)
        tools: list[str] = _extract_tools(fm)
        skills: list[str] = _extract_skills(fm)
        permission_mode: str | None = _extract_permission_mode(fm)
        max_iteration_per_run: int | None = _extract_max_iteration_per_run(fm)
        mcp_servers: dict[str, Any] | None = _extract_mcp_servers(fm)
        profile_store_dir: str | None = _extract_profile_store_dir(fm)
        hooks: HookConfig | None = _extract_hooks(fm)

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
            mcp_servers=mcp_servers,
            hooks=hooks,
            profile_store_dir=profile_store_dir,
            system_prompt=content,
            source=str(agent_path),
            when_to_use_examples=when_to_use_examples,
            metadata=metadata,
        )
