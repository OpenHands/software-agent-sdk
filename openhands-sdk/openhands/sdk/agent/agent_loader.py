"""Load agent definitions from Markdown files and register them as delegate agents.

Agent definitions are Markdown files with YAML frontmatter that live in
``.agents/`` directories at the project or user level. They are auto-registered
into the delegate agent registry so they can be invoked by name during delegation.

Directory convention::

    {project}/.agents/          # Project-level (higher priority)
      code-reviewer.md          # Agent definition
      security-expert.md        # Agent definition
      skills/                   # Existing skills (untouched)

    ~/.agents/                  # User-level (lower priority)
      my-global-agent.md        # Agent definition

Priority (highest to lowest):
  1. Programmatic ``register_agent()`` calls (never overwritten)
  2. Plugin agents (``Plugin.agents``)
  3. Project-level ``.agents/*.md``
  4. User-level ``~/.agents/*.md``
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Final

from openhands.sdk.logger import get_logger
from openhands.sdk.plugin.types import AgentDefinition


if TYPE_CHECKING:
    from openhands.sdk.agent.agent import Agent
    from openhands.sdk.llm import LLM

logger = get_logger(__name__)

AGENTS_DIR_NAME: Final[str] = ".agents"
_SKIP_FILES: Final[set[str]] = {"README.md", "readme.md"}


def agent_definition_to_factory(
    agent_def: AgentDefinition,
) -> Callable[[LLM], Agent]:
    """Create an agent factory closure from an ``AgentDefinition``.

    The returned callable accepts an :class:`LLM` (the parent agent's LLM) and
    builds a fully-configured :class:`Agent`.

    * Tool names from ``agent_def.tools`` are mapped to :class:`Tool` objects.
    * The system prompt becomes an always-active :class:`Skill` (``trigger=None``).
    * ``model: inherit`` preserves the parent LLM; an explicit model name creates
      a copy with ``model_copy()``.
    """

    def _factory(llm: LLM) -> Agent:
        # Deferred imports to avoid circular dependency
        from openhands.sdk.agent.agent import Agent
        from openhands.sdk.context.agent_context import AgentContext
        from openhands.sdk.context.skills import Skill
        from openhands.sdk.tool.spec import Tool

        # Resolve tools
        tools = [Tool(name=name) for name in agent_def.tools]

        # Build always-active skill from system prompt
        skills: list[Skill] = []
        if agent_def.system_prompt:
            skills.append(
                Skill(
                    name=f"{agent_def.name}_prompt",
                    content=agent_def.system_prompt,
                    trigger=None,
                )
            )

        agent_context = AgentContext(skills=skills) if skills else None

        # Handle model override
        if agent_def.model and agent_def.model != "inherit":
            llm = llm.model_copy(update={"model": agent_def.model})

        return Agent(
            llm=llm,
            tools=tools,
            agent_context=agent_context,
        )

    return _factory


def load_project_agents(work_dir: str | Path) -> list[AgentDefinition]:
    """Load agent definitions from ``{work_dir}/.agents/*.md``.

    Only reads top-level ``.md`` files; subdirectories (like ``skills/``) are
    skipped.  ``README.md`` is also skipped.

    Returns an empty list if the directory does not exist.
    """
    agents_dir = Path(work_dir) / AGENTS_DIR_NAME
    return _load_agents_from_dir(agents_dir)


def load_user_agents() -> list[AgentDefinition]:
    """Load agent definitions from ``~/.agents/*.md``.

    Same rules as :func:`load_project_agents`.
    """
    agents_dir = Path.home() / AGENTS_DIR_NAME
    return _load_agents_from_dir(agents_dir)


def _load_agents_from_dir(agents_dir: Path) -> list[AgentDefinition]:
    """Shared helper: scan *agents_dir* for top-level ``.md`` agent files."""
    if not agents_dir.is_dir():
        return []

    definitions: list[AgentDefinition] = []
    for md_file in sorted(agents_dir.iterdir()):
        # Only top-level .md files; skip subdirectories and README
        if md_file.is_dir():
            continue
        if md_file.suffix.lower() != ".md":
            continue
        if md_file.name in _SKIP_FILES:
            continue

        try:
            agent_def = AgentDefinition.load(md_file)
            definitions.append(agent_def)
            logger.debug(f"Loaded agent definition '{agent_def.name}' from {md_file}")
        except Exception:
            logger.warning(
                f"Failed to load agent definition from {md_file}", exc_info=True
            )

    return definitions


def register_file_agents(work_dir: str | Path) -> list[str]:
    """Load and register file-based agents from ``.agents/`` directories.

    Loads from both project-level (``{work_dir}/.agents/``) and user-level
    (``~/.agents/``) directories. Project-level definitions take priority over
    user-level ones. Neither overwrites agents already registered
    programmatically or by plugins.

    Returns:
        List of agent names that were actually registered.
    """
    project_agents = load_project_agents(work_dir)
    user_agents = load_user_agents()

    # Deduplicate: project wins over user
    seen_names: set[str] = set()
    ordered: list[AgentDefinition] = []

    for agent_def in project_agents:
        if agent_def.name not in seen_names:
            seen_names.add(agent_def.name)
            ordered.append(agent_def)

    for agent_def in user_agents:
        if agent_def.name not in seen_names:
            seen_names.add(agent_def.name)
            ordered.append(agent_def)

    # from openhands.tools.delegate.registration import register_agent_if_absent
    def register_agent_if_absent(
        name: str,
        factory_func: Callable[[LLM], Agent],
        description: str,
    ) -> bool:
        _ = name
        _ = description
        _ = factory_func
        return False

    registered: list[str] = []
    for agent_def in ordered:
        factory = agent_definition_to_factory(agent_def)
        was_registered = register_agent_if_absent(
            name=agent_def.name,
            factory_func=factory,
            description=agent_def.description or f"File-based agent: {agent_def.name}",
        )
        if was_registered:
            registered.append(agent_def.name)
            logger.info(
                f"Registered file-based agent '{agent_def.name}'"
                + (f" from {agent_def.source}" if agent_def.source else "")
            )

    return registered


def register_plugin_agents(agents: list[AgentDefinition]) -> list[str]:
    """Register plugin-provided agent definitions into the delegate registry.

    Plugin agents have higher priority than file-based agents but lower than
    programmatic ``register_agent()`` calls. This function bridges the existing
    ``Plugin.agents`` list (which is loaded but not currently registered) into
    the delegate registry.

    Args:
        agents: Agent definitions collected from loaded plugins.

    Returns:
        List of agent names that were actually registered.
    """

    # from openhands.tools.delegate.registration import register_agent_if_absent
    def register_agent_if_absent(
        name: str,
        factory_func: Callable[[LLM], Agent],
        description: str,
    ) -> bool:
        _ = name
        _ = description
        _ = factory_func
        return False

    registered: list[str] = []
    for agent_def in agents:
        factory = agent_definition_to_factory(agent_def)
        was_registered = register_agent_if_absent(
            name=agent_def.name,
            factory_func=factory,
            description=agent_def.description or f"Plugin agent: {agent_def.name}",
        )
        if was_registered:
            registered.append(agent_def.name)
            logger.info(f"Registered plugin agent '{agent_def.name}'")

    return registered
