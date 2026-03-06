"""Schema for Markdown-based agent definition files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

import frontmatter
from pydantic import BaseModel, Field


KNOWN_FIELDS: Final[set[str]] = {
    "name",
    "description",
    "model",
    "color",
    "tools",
    "skills",
    "max_iteration_per_run",
    "permission_mode",
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
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata from frontmatter"
    )

    def get_confirmation_policy(self):
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
                raise RuntimeError

    @classmethod
    def load(cls, agent_path: Path) -> AgentDefinition:
        """Load an agent definition from a Markdown file.

        Agent Markdown files have YAML frontmatter with:
        - name: Agent name
        - description: Description with optional <example> tags for triggering
        - tools (optional): List of allowed tools
        - skills (optional): Comma-separated skill names or list of skill names
        - model (optional): Model profile to use (default: 'inherit')
        - color (optional): Display color
        - permission_mode (optional): How the subagent handles permissions
          ('always_confirm', 'never_confirm', 'confirm_risky'). None inherits parent.
        - max_iterations_per_run: Max iteration per run

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
            system_prompt=content,
            source=str(agent_path),
            when_to_use_examples=when_to_use_examples,
            metadata=metadata,
        )
