"""Type definitions for Plugin module."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import frontmatter
from pydantic import BaseModel, Field


class PluginAuthor(BaseModel):
    """Author information for a plugin."""

    name: str = Field(description="Author's name")
    email: str | None = Field(default=None, description="Author's email address")

    @classmethod
    def from_string(cls, author_str: str) -> PluginAuthor:
        """Parse author from string format 'Name <email>'."""
        if "<" in author_str and ">" in author_str:
            name = author_str.split("<")[0].strip()
            email = author_str.split("<")[1].split(">")[0].strip()
            return cls(name=name, email=email)
        return cls(name=author_str.strip())


class PluginManifest(BaseModel):
    """Plugin manifest from plugin.json."""

    name: str = Field(description="Plugin name")
    version: str = Field(default="1.0.0", description="Plugin version")
    description: str = Field(default="", description="Plugin description")
    author: PluginAuthor | None = Field(default=None, description="Plugin author")

    model_config = {"extra": "allow"}


def _extract_examples(description: str) -> list[str]:
    """Extract <example> tags from description for agent triggering."""
    pattern = r"<example>(.*?)</example>"
    matches = re.findall(pattern, description, re.DOTALL | re.IGNORECASE)
    return [m.strip() for m in matches if m.strip()]


class AgentDefinition(BaseModel):
    """Agent definition loaded from markdown file.

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
        """Load an agent definition from a markdown file.

        Agent markdown files have YAML frontmatter with:
        - name: Agent name
        - description: Description with optional <example> tags for triggering
        - model: Model to use (default: 'inherit')
        - color: Display color
        - tools: List of allowed tools

        The body of the markdown is the system prompt.

        Args:
            agent_path: Path to the agent markdown file.

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

        # Extract whenToUse examples from description
        when_to_use_examples = _extract_examples(description)

        # Remove known fields from metadata to get extras
        known_fields = {"name", "description", "model", "color", "tools"}
        metadata = {k: v for k, v in fm.items() if k not in known_fields}

        return cls(
            name=name,
            description=description,
            model=model,
            color=color,
            tools=tools,
            system_prompt=content,
            source=str(agent_path),
            when_to_use_examples=when_to_use_examples,
            metadata=metadata,
        )


class CommandDefinition(BaseModel):
    """Command definition loaded from markdown file.

    Commands are slash commands that users can invoke directly.
    They define instructions for the agent to follow.
    """

    name: str = Field(description="Command name (from filename, e.g., 'review')")
    description: str = Field(default="", description="Command description")
    argument_hint: str | None = Field(
        default=None, description="Hint for command arguments"
    )
    allowed_tools: list[str] = Field(
        default_factory=list, description="List of allowed tools for this command"
    )
    content: str = Field(default="", description="Command instructions/content")
    source: str | None = Field(
        default=None, description="Source file path for this command"
    )
    # Raw frontmatter for any additional fields
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata from frontmatter"
    )

    @classmethod
    def load(cls, command_path: Path) -> CommandDefinition:
        """Load a command definition from a markdown file.

        Command markdown files have YAML frontmatter with:
        - description: Command description
        - argument-hint: Hint for command arguments (string or list)
        - allowed-tools: List of allowed tools

        The body of the markdown is the command instructions.

        Args:
            command_path: Path to the command markdown file.

        Returns:
            Loaded CommandDefinition instance.
        """
        with open(command_path) as f:
            post = frontmatter.load(f)

        # Extract frontmatter fields with proper type handling
        fm = post.metadata
        name = command_path.stem  # Command name from filename
        description = str(fm.get("description", ""))
        argument_hint_raw = fm.get("argument-hint") or fm.get("argumentHint")
        allowed_tools_raw = fm.get("allowed-tools") or fm.get("allowedTools") or []

        # Handle argument_hint as list (join with space) or string
        argument_hint: str | None
        if isinstance(argument_hint_raw, list):
            argument_hint = " ".join(str(h) for h in argument_hint_raw)
        elif argument_hint_raw is not None:
            argument_hint = str(argument_hint_raw)
        else:
            argument_hint = None

        # Ensure allowed_tools is a list of strings
        allowed_tools: list[str]
        if isinstance(allowed_tools_raw, str):
            allowed_tools = [allowed_tools_raw]
        elif isinstance(allowed_tools_raw, list):
            allowed_tools = [str(t) for t in allowed_tools_raw]
        else:
            allowed_tools = []

        # Remove known fields from metadata to get extras
        known_fields = {
            "description",
            "argument-hint",
            "argumentHint",
            "allowed-tools",
            "allowedTools",
        }
        metadata = {k: v for k, v in fm.items() if k not in known_fields}

        return cls(
            name=name,
            description=description,
            argument_hint=argument_hint,
            allowed_tools=allowed_tools,
            content=post.content.strip(),
            source=str(command_path),
            metadata=metadata,
        )
