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
    "profile_store_dir",
    "condenser",
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


_CONDENSER_VALID_KEYS: Final[set[str]] = {
    "max_size",
    "max_tokens",
    "keep_first",
    "minimum_progress",
    "hard_context_reset_max_retries",
    "hard_context_reset_context_scaling",
}


def _extract_condenser(fm: dict[str, object]) -> dict[str, Any] | None:
    """Extract condenser configuration from frontmatter."""
    condenser_raw = fm.get("condenser")
    if condenser_raw is None:
        return None
    if not isinstance(condenser_raw, dict):
        raise ValueError(
            f"condenser must be a mapping of configuration parameters, "
            f"got {type(condenser_raw)}"
        )
    unknown_keys = set(condenser_raw.keys()) - _CONDENSER_VALID_KEYS
    if unknown_keys:
        raise ValueError(
            f"Unknown condenser parameter(s): {sorted(unknown_keys)}. "
            f"Valid parameters are: {sorted(_CONDENSER_VALID_KEYS)}"
        )
    return condenser_raw


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
    max_iteration_per_run: int | None = Field(
        default=None,
        description="Maximum iterations per run. "
        "It must be strictly positive, or None for default.",
        gt=0,
    )
    condenser: dict[str, Any] | None = Field(
        default=None,
        description="Condenser configuration for this agent. "
        "Parameters are passed to LLMSummarizingCondenser (e.g., max_size, ...). "
        "The condenser LLM is inherited from the agent's LLM.",
        examples=[{"max_size": 100, "keep_first": 2}],
    )
    profile_store_dir: str | None = Field(
        default=None,
        description="Path to the directory where LLM profiles are stored. "
        "If None, the default profile store directory is used.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata from frontmatter"
    )

    @classmethod
    def load(cls, agent_path: Path) -> AgentDefinition:
        """Load an agent definition from a Markdown file.

        Agent Markdown files have YAML frontmatter with:
        - name: Agent name
        - description: Description with optional <example> tags for triggering
        - tools (optional): List of allowed tools
        - skills (optional): Comma-separated skill names or list of skill names
        - condenser (optional): Condenser configuration (e.g., max_size, keep_first)
        - model (optional): Model profile to use (default: 'inherit')
        - color (optional): Display color
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
        max_iteration_per_run: int | None = _extract_max_iteration_per_run(fm)
        condenser: dict[str, Any] | None = _extract_condenser(fm)
        profile_store_dir: str | None = _extract_profile_store_dir(fm)

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
            max_iteration_per_run=max_iteration_per_run,
            condenser=condenser,
            profile_store_dir=profile_store_dir,
            system_prompt=content,
            source=str(agent_path),
            when_to_use_examples=when_to_use_examples,
            metadata=metadata,
        )
