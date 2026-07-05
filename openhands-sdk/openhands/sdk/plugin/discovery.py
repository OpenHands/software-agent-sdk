"""Discovery of locally-available plugins for OpenHands SDK.

Mirrors the user/project skill discovery in ``skills/skill.py`` but operates on
``Plugin`` objects (loaded via ``Plugin.load``) instead of skills.

These functions build the *ambient* plugin set that auto-loads into interactive
conversations (see ``LocalConversation._ensure_plugins_loaded``): plugins found
in the user's home directories, in the project/workspace directories, and the
enabled installed plugins managed via ``install_plugin`` / ``enable_plugin``.
This is the plugin analogue of how installed/local skills already auto-load.
"""

from __future__ import annotations

from pathlib import Path

from openhands.sdk.logger import get_logger
from openhands.sdk.plugin.installed import (
    get_installed_plugins_dir,
    load_installed_plugins,
)
from openhands.sdk.plugin.plugin import Plugin
from openhands.sdk.skills.skill import _find_git_repo_root


logger = get_logger(__name__)


# User-level plugin directories, in order of precedence (earlier wins on a
# name conflict). Mirrors USER_SKILLS_DIRS.
USER_PLUGINS_DIRS = [
    Path.home() / ".agents" / "plugins",
    Path.home() / ".openhands" / "plugins",
]

# Project-level plugin subdirectories scanned under each search root.
PROJECT_PLUGINS_SUBDIRS = [
    Path(".agents") / "plugins",
    Path(".openhands") / "plugins",
]


def _load_plugins_from_dir(plugins_dir: Path, skip_dirs: set[Path]) -> list[Plugin]:
    """Load every plugin subdirectory in ``plugins_dir``.

    Mirrors ``Plugin.load_all`` but skips any directory in ``skip_dirs``. This is
    needed because the installed-plugins store lives at
    ``~/.openhands/plugins/installed`` — a child of ``~/.openhands/plugins`` — and
    must not be loaded as if it were a plugin (it has no manifest, so it would be
    mis-loaded as a bogus plugin named "installed"). Failures to load an
    individual plugin are logged and skipped.
    """
    if not plugins_dir.is_dir():
        return []

    plugins: list[Plugin] = []
    for item in sorted(plugins_dir.iterdir()):
        if not item.is_dir():
            continue
        if item.resolve() in skip_dirs:
            continue
        try:
            plugins.append(Plugin.load(item))
        except Exception as e:
            logger.warning(f"Failed to load plugin from {item}: {e}")
    return plugins


def _merge_plugins_by_name(
    new_plugins: list[Plugin],
    seen_names: set[str],
    out: list[Plugin],
    source_label: str,
) -> None:
    """Append ``new_plugins`` to ``out``, skipping names already in ``seen_names``.

    Earlier callers take precedence for duplicate names (mutates ``seen_names``
    and ``out`` in place).
    """
    for plugin in new_plugins:
        if plugin.name in seen_names:
            logger.warning(
                f"Skipping duplicate plugin '{plugin.name}' from {source_label}"
            )
            continue
        seen_names.add(plugin.name)
        out.append(plugin)


def load_user_plugins() -> list[Plugin]:
    """Load plugins from the user's home directories plus enabled installed plugins.

    Scans ``~/.agents/plugins`` then ``~/.openhands/plugins`` (earlier wins on a
    name conflict), then appends enabled installed plugins from
    ``~/.openhands/plugins/installed`` (lower precedence than directory plugins).
    The installed store is excluded from the directory scan so it is not loaded
    as a plugin itself.

    Mirrors ``load_user_skills``. Returns an empty list if nothing is found or
    loading fails.
    """
    all_plugins: list[Plugin] = []
    seen_names: set[str] = set()
    # Resolve via get_installed_plugins_dir() (not a captured constant) so the
    # skip tracks the same install store that load_installed_plugins() reads.
    installed_dir = get_installed_plugins_dir().resolve()

    for plugins_dir in USER_PLUGINS_DIRS:
        found = _load_plugins_from_dir(plugins_dir, skip_dirs={installed_dir})
        _merge_plugins_by_name(found, seen_names, all_plugins, str(plugins_dir))

    # Enabled installed plugins (lower precedence than directory plugins).
    try:
        for plugin in load_installed_plugins():
            if plugin.name not in seen_names:
                seen_names.add(plugin.name)
                all_plugins.append(plugin)
    except Exception as e:
        logger.warning(f"Failed to load installed plugins: {e}")

    logger.debug(
        f"Loaded {len(all_plugins)} user plugins: {[p.name for p in all_plugins]}"
    )
    return all_plugins


def load_project_plugins(work_dir: str | Path) -> list[Plugin]:
    """Load plugins from project-specific directories.

    Scans ``{root}/.agents/plugins`` and ``{root}/.openhands/plugins`` for the
    working directory and (if different) the enclosing Git repository root, with
    the working directory taking precedence on a name conflict.

    Mirrors ``load_project_skills``. Returns an empty list if nothing is found or
    loading fails.
    """
    work_dir = Path(work_dir)

    all_plugins: list[Plugin] = []
    seen_names: set[str] = set()

    git_root = _find_git_repo_root(work_dir)

    # Working dir takes precedence (more local rules override repo-root rules).
    search_roots: list[Path] = [work_dir]
    if git_root is not None and git_root != work_dir:
        search_roots.append(git_root)

    for root in search_roots:
        for subdir in PROJECT_PLUGINS_SUBDIRS:
            plugins_dir = root / subdir
            found = _load_plugins_from_dir(plugins_dir, skip_dirs=set())
            _merge_plugins_by_name(found, seen_names, all_plugins, str(plugins_dir))

    logger.debug(
        f"Loaded {len(all_plugins)} project plugins: {[p.name for p in all_plugins]}"
    )
    return all_plugins


def load_available_plugins(
    work_dir: str | Path | None = None,
    *,
    include_user: bool = False,
    include_project: bool = False,
) -> dict[str, Plugin]:
    """Load and merge locally-available plugins with consistent precedence.

    Precedence (later overrides earlier via dict updates):
        user (lowest) → project (highest)

    The user set already folds in enabled installed plugins (lowest within the
    user set). This is the single entry-point for building the *ambient* plugin
    set merged into a conversation, mirroring ``load_available_skills``.

    Args:
        work_dir: Project/working directory for project plugins. When None,
            project plugins are skipped regardless of ``include_project``.
        include_user: Load user-level plugins (``~/.agents/plugins`` etc.) and
            enabled installed plugins.
        include_project: Load project-level plugins (requires ``work_dir``).

    Returns:
        Dict mapping plugin name → Plugin, with higher-precedence sources
        overriding lower ones.
    """
    available: dict[str, Plugin] = {}

    if include_user:
        try:
            for plugin in load_user_plugins():
                available[plugin.name] = plugin
        except Exception as e:
            logger.warning(f"Failed to load user plugins: {e}")

    if include_project and work_dir:
        try:
            for plugin in load_project_plugins(work_dir):
                available[plugin.name] = plugin
        except Exception as e:
            logger.warning(f"Failed to load project plugins: {e}")

    return available
