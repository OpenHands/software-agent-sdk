"""Schema to load md-based agents definition files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import frontmatter
from pydantic import BaseModel, Field


def _extract_examples(description: str) -> list[str]:
    """Extract <example> tags from description for agent triggering."""
    pattern = r"<example>(.*?)</example>"
    matches = re.findall(pattern, description, re.DOTALL | re.IGNORECASE)
    return [m.strip() for m in matches if m.strip()]


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
    system_prompt: str = Field(default="", description="System prompt content")
    allowed_commands: list[str] = Field(
        default_factory=list,
        description="List of allowed shell commands for this agent",
    )
    source: str | None = Field(
        default=None, description="Source file path for this agent"
    )
    # whenToUse examples extracted from description
    when_to_use_examples: list[str] = Field(
        default_factory=list,
        description="Examples of when to use this agent (for triggering)",
    )
    # Raw frontmatter for any additional fields
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
        - model (optional: Model to use (default: 'inherit')
        - color (optional): Display color

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
        name = str(fm.get("name", agent_path.stem))
        description = str(fm.get("description", ""))
        model = str(fm.get("model", "inherit"))
        color_raw = fm.get("color")
        color: str | None = str(color_raw) if color_raw is not None else None
        tools_raw = fm.get("tools", [])

        # Ensure tools is a list of strings
        tools: list[str]
        if isinstance(tools_raw, str):
            tools = [tools_raw]
        elif isinstance(tools_raw, list):
            tools = [str(t) for t in tools_raw]
        else:
            tools = []

        # Parse allowed_commands (supports both snake_case and kebab-case)
        allowed_commands_raw = (
            fm.get("allowed_commands") or fm.get("allowed-commands") or []
        )
        allowed_commands: list[str]
        if isinstance(allowed_commands_raw, str):
            allowed_commands = [allowed_commands_raw]
        elif isinstance(allowed_commands_raw, list):
            allowed_commands = [str(c) for c in allowed_commands_raw]
        else:
            allowed_commands = []

        # Extract whenToUse examples from description
        when_to_use_examples = _extract_examples(description)

        # Remove known fields from metadata to get extras
        known_fields = {
            "name",
            "description",
            "model",
            "color",
            "tools",
            "allowed_commands",
            "allowed-commands",
        }
        metadata = {k: v for k, v in fm.items() if k not in known_fields}

        return cls(
            name=name,
            description=description,
            model=model,
            color=color,
            tools=tools,
            allowed_commands=allowed_commands,
            system_prompt=content,
            source=str(agent_path),
            when_to_use_examples=when_to_use_examples,
            metadata=metadata,
        )
