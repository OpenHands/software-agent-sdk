"""Load agent definitions from Markdown files and register them as delegate agents.

Agent definitions are Markdown files with YAML frontmatter that live in
``.agents/agents`` or ``.openhands/agents`` directories at the project or user level.
They are auto-registered into the delegate agent registry so they can be
invoked by name during delegation.

Directory convention (in priority order)::

    {project}/                      # Project-level, primary (highest file priority)
        .agents/
            agents/
                code-reviewer.md    # Agent definition
                security-expert.md  # Agent definition

    {project}/
        .openhands/
            agents/
                code-reviewer.md

    ~/.agents/                      # User-level, primary
        agents/
            my-global-agent.md

    ~/.openhands/               # User-level, legacy (lowest file priority)
        agents/
            my-global-agent.md

Priority (highest to lowest):
  1. Programmatic ``register_agent()`` calls (never overwritten)
  2. Plugin agents (``Plugin.agents``)
  3. Project-level ``.agents/agents/*.md``
  4. Project-level ``.openhands/agents/*.md``
  5. User-level ``~/.agents/agents/*.md``
  6. User-level ``~/.openhands/agents/*.md``
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from openhands.sdk.logger import get_logger
from openhands.sdk.subagent.schema import AgentDefinition


logger = get_logger(__name__)


# Directories to scan for agent definitions, in priority order.
# First match wins when the same agent name appears in multiple directories.
_FILE_BASED_AGENTS_DIR: Final[list[str]] = [
    ".agents/agents",
    ".openhands/agents",
]
# file to skip analyzing when searching for agents
_SKIP_FILES: Final[set[str]] = {"README.md", "readme.md"}


def load_project_agents(project_dir: str | Path) -> list[AgentDefinition]:
    """Load agent definitions from project-level directories.

    Searches for
        - project_dir/.agents/agents and
        - project_dir/.openhands/agents (in that order).
    Note that `.agents/agents` definitions take precedence for duplicate names.

    Only reads top-level `.md` files; subdirectories (like `skills/`) are
    skipped.  `README.md` files are also skipped.

    Args:
        project_dir: project directory

    Returns:
        an empy list if no directories exist.
    """
    project_dir = Path(project_dir)
    return _load_agents_from_dirs([project_dir / d for d in _FILE_BASED_AGENTS_DIR])


def load_user_agents() -> list[AgentDefinition]:
    """Load agent definitions from user-level directories.

    Searches for
        - ~/.agents/agents and
        - ~/.openhands/agents (in that order).
    note that `.agents/agents` definitions take precedence for duplicate names.

    Same file-level rules as `load_project_agents`.

    Returns:
        an empy list if no directories exist.
    """
    home = Path.home()
    return _load_agents_from_dirs([home / d for d in _FILE_BASED_AGENTS_DIR])


def _load_agents_from_dirs(dirs: list[Path]) -> list[AgentDefinition]:
    """Load agents from multiple directories with first-wins deduplication.

    Directories are scanned in order; if the same agent name appears in a
    later directory it is silently skipped.
    """
    seen_names: set[str] = set()
    result: list[AgentDefinition] = []
    for agents_dir in dirs:
        for agent_def in _load_agents_from_dir(agents_dir):
            if agent_def.name not in seen_names:
                seen_names.add(agent_def.name)
                result.append(agent_def)
            else:
                logger.debug(
                    f"Skipping duplicate agent '{agent_def.name}' from {agents_dir}"
                )
    return result


def _load_agents_from_dir(agents_dir: Path) -> list[AgentDefinition]:
    """Shared helper: scan *agents_dir* for top-level `.md` agent files."""
    if not agents_dir.is_dir():
        return []

    definitions: list[AgentDefinition] = []
    for md_file in sorted(agents_dir.iterdir()):
        # Only top-level .md files; skip subdirectories and README
        if (
            md_file.is_dir()
            or md_file.suffix.lower() != ".md"
            or md_file.name in _SKIP_FILES
        ):
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
