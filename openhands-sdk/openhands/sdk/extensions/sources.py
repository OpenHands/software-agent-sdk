"""Extension sources — functions that produce an Extensions bundle.

Each function loads extensions from a single source and returns an
:class:`~openhands.sdk.extensions.extensions.Extensions` bundle.
Callers merge the results with :py:meth:`Extensions.collapse` to get
a final resolved set.

.. note::

   This module is **not** imported from ``extensions/__init__.py``
   (which must stay import-free to avoid circular imports during SDK
   init).  Import directly::

       from openhands.sdk.extensions.sources import from_plugin
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from openhands.sdk.extensions.extensions import Extensions
from openhands.sdk.hooks.config import HookConfig
from openhands.sdk.logger import get_logger
from openhands.sdk.skills.skill import (
    DEFAULT_MARKETPLACE_PATH,
    PUBLIC_SKILLS_BRANCH,
    PUBLIC_SKILLS_REPO,
    Skill,
    load_project_skills,
    load_public_skills,
    load_user_skills,
)


if TYPE_CHECKING:
    from openhands.sdk.plugin.plugin import Plugin
    from openhands.sdk.subagent.schema import AgentDefinition

logger = get_logger(__name__)

DEFAULT_HOOKS_FILENAME = ".openhands/hooks.json"


# ------------------------------------------------------------------
# Plugin source
# ------------------------------------------------------------------


def from_plugin(plugin: Plugin) -> Extensions:
    """Build an :class:`Extensions` from a loaded :class:`Plugin`.

    Skills include both explicit skills and command-derived skills
    (via :py:meth:`Plugin.get_all_skills`).
    """
    return Extensions(
        skills=plugin.get_all_skills(),
        hooks=plugin.hooks,
        mcp_config=plugin.mcp_config or {},
        agents=list(plugin.agents),
    )


# ------------------------------------------------------------------
# Project source
# ------------------------------------------------------------------


def from_project(
    work_dir: str | Path,
    *,
    hooks_path: str | Path | None = None,
) -> Extensions:
    """Load extensions from the project / workspace directory.

    Loads:
    - Project skills from ``.agents/skills/``, ``.openhands/skills/``,
      third-party files (``AGENTS.md``, ``.cursorrules``, etc.)
    - Project hooks from *hooks_path*

    Args:
        work_dir: Project / workspace directory.
        hooks_path: Path to hooks JSON file.  Defaults to
            ``{work_dir}/.openhands/hooks.json``.
    """
    work_dir = Path(work_dir)
    resolved_hooks_path = (
        Path(hooks_path) if hooks_path else work_dir / DEFAULT_HOOKS_FILENAME
    )

    skills = _load_skills_safe(
        lambda: load_project_skills(work_dir),
        context=f"project skills from {work_dir}",
    )
    hooks = _load_hooks_safe(resolved_hooks_path)

    return Extensions(
        skills=skills,
        hooks=hooks,
    )


# ------------------------------------------------------------------
# User source
# ------------------------------------------------------------------


def from_user(
    *,
    hooks_path: str | Path | None = None,
) -> Extensions:
    """Load extensions from user-level directories.

    Loads:
    - User skills from ``~/.agents/skills/``, ``~/.openhands/skills/``,
      installed skills, etc.
    - User hooks from *hooks_path*

    Args:
        hooks_path: Path to hooks JSON file.  Defaults to
            ``~/.openhands/hooks.json``.
    """
    resolved_hooks_path = (
        Path(hooks_path) if hooks_path else Path.home() / DEFAULT_HOOKS_FILENAME
    )

    skills = _load_skills_safe(load_user_skills, context="user skills")
    hooks = _load_hooks_safe(resolved_hooks_path)

    return Extensions(
        skills=skills,
        hooks=hooks,
    )


# ------------------------------------------------------------------
# Marketplace / public source
# ------------------------------------------------------------------


def from_marketplace(
    repo_url: str = PUBLIC_SKILLS_REPO,
    branch: str = PUBLIC_SKILLS_BRANCH,
    marketplace_path: str | None = DEFAULT_MARKETPLACE_PATH,
) -> Extensions:
    """Load extensions from a skills marketplace repository.

    A marketplace is a git repository containing a ``skills/`` directory
    and an optional marketplace JSON manifest that filters which skills
    are included.

    Args:
        repo_url: Git URL of the marketplace repository.
        branch: Branch to load from.
        marketplace_path: Relative path to a marketplace JSON file
            within the repository.  ``None`` loads all skills.
    """
    try:
        skills = load_public_skills(
            repo_url=repo_url,
            branch=branch,
            marketplace_path=marketplace_path,
        )
    except Exception as e:
        logger.warning("Failed to load marketplace skills from %s: %s", repo_url, e)
        skills = []

    return Extensions(skills=skills)


def from_public() -> Extensions:
    """Load extensions from the official OpenHands public marketplace.

    Convenience wrapper around :func:`from_marketplace` using the
    default OpenHands extensions repository and marketplace.
    """
    return from_marketplace()


# ------------------------------------------------------------------
# Inline source
# ------------------------------------------------------------------


def from_inline(
    *,
    skills: list[Skill] | None = None,
    hooks: HookConfig | None = None,
    mcp_config: dict[str, Any] | None = None,
    agents: list[AgentDefinition] | None = None,
) -> Extensions:
    """Wrap explicitly-provided values into an :class:`Extensions`.

    This is the backward-compatibility bridge: callers that already have
    skills on ``AgentContext``, a ``hook_config``, or ``agent.mcp_config``
    can wrap them into an ``Extensions`` bundle for merging.
    """
    return Extensions(
        skills=skills or [],
        hooks=hooks if hooks and not hooks.is_empty() else None,
        mcp_config=mcp_config or {},
        agents=agents or [],
    )


# ------------------------------------------------------------------
# Shared helpers
# ------------------------------------------------------------------


def _load_skills_safe(
    loader: Any,
    *,
    context: str,
) -> list[Skill]:
    """Call *loader* and return its result, or ``[]`` on failure."""
    try:
        return loader()
    except Exception as e:
        logger.warning("Failed to load %s: %s", context, e)
        return []


def _load_hooks_safe(hooks_path: Path) -> HookConfig | None:
    """Load a hooks JSON file, returning ``None`` when absent or broken."""
    if not hooks_path.exists():
        return None
    try:
        config = HookConfig.load(path=hooks_path)
        return config if not config.is_empty() else None
    except Exception as e:
        logger.warning("Failed to load hooks from %s: %s", hooks_path, e)
        return None
