"""Plugins service for OpenHands Agent Server.

Business logic for two related concerns, both mirroring their skills
counterparts (``skills_service.py``) so the router stays focused on HTTP:

* Installed-plugin management — thin wrappers over the SDK's installed-plugins
  subsystem (``openhands.sdk.plugin``) — plus listing the locally-available
  plugins.
* The *plugins-only* marketplace catalog. It returns only true plugins from the
  OpenHands extensions marketplace — entries whose ``source`` lives under
  ``./plugins/`` — each carrying attachable ``PluginSource`` coordinates
  (``source`` / ``ref`` / ``repo_path``) plus an ``installed`` flag, so the
  front-end can drive both *attach* and *install* and show install state.
"""

import os
from pathlib import Path

from pydantic import BaseModel

from openhands.agent_server.marketplace_snapshot import load_marketplace_snapshot
from openhands.sdk.logger import get_logger
from openhands.sdk.marketplace import Marketplace
from openhands.sdk.plugin import (
    InstalledPluginInfo,
    Plugin,
    disable_plugin,
    enable_plugin,
    get_installed_plugin,
    install_plugin,
    list_installed_plugins,
    uninstall_plugin,
    update_plugin,
)
from openhands.sdk.skills.skill import DEFAULT_MARKETPLACE_PATH
from openhands.sdk.utils.path import to_posix_path


logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Installed-plugin management
# ---------------------------------------------------------------------------


def service_install_plugin(
    source: str,
    ref: str | None = None,
    repo_path: str | None = None,
    force: bool = False,
    installed_dir: Path | None = None,
) -> InstalledPluginInfo:
    """Install a plugin from a source into the installed-plugins directory."""
    return install_plugin(
        source=source,
        ref=ref,
        repo_path=repo_path,
        force=force,
        installed_dir=installed_dir,
    )


def service_uninstall_plugin(name: str, installed_dir: Path | None = None) -> bool:
    """Uninstall a plugin by name. Returns False if it wasn't installed."""
    return uninstall_plugin(name, installed_dir=installed_dir)


def service_enable_plugin(name: str, installed_dir: Path | None = None) -> bool:
    """Enable an installed plugin. Returns False if it isn't installed."""
    return enable_plugin(name, installed_dir=installed_dir)


def service_disable_plugin(name: str, installed_dir: Path | None = None) -> bool:
    """Disable an installed plugin. Returns False if it isn't installed."""
    return disable_plugin(name, installed_dir=installed_dir)


def service_list_installed_plugins(
    installed_dir: Path | None = None,
) -> list[InstalledPluginInfo]:
    """List all installed plugins (enabled and disabled)."""
    return list_installed_plugins(installed_dir=installed_dir)


def service_get_installed_plugin(
    name: str, installed_dir: Path | None = None
) -> InstalledPluginInfo | None:
    """Get a specific installed plugin, or None if it isn't installed."""
    return get_installed_plugin(name, installed_dir=installed_dir)


def service_update_plugin(
    name: str, installed_dir: Path | None = None
) -> InstalledPluginInfo | None:
    """Update an installed plugin, or None if it isn't installed."""
    return update_plugin(name, installed_dir=installed_dir)


def service_list_available_plugins(
    load_user: bool = True,
    load_project: bool = True,
    project_dir: str | None = None,
) -> list[Plugin]:
    """List locally-available plugins (enabled installed + user/project dirs).

    ``load_available_plugins`` is provided by the "Wire installed + local plugin
    auto-load" ticket (``openhands.sdk.plugin.discovery``). It is imported lazily
    so this module imports cleanly before that ticket is merged; this endpoint
    becomes functional once it lands.
    """
    from openhands.sdk.plugin import load_available_plugins  # type: ignore

    available = load_available_plugins(
        work_dir=project_dir,
        include_user=load_user,
        include_project=load_project,
    )
    return list(available.values())


# ---------------------------------------------------------------------------
# Plugin contents (bundled skills + file listing)
# ---------------------------------------------------------------------------

# Directories never listed in a plugin's file tree. Whole-repo installs copy
# the fetched clone verbatim (including ``.git``), which would flood the
# listing with repository internals.
_PLUGIN_FILES_EXCLUDED_DIRS = {".git"}
# Upper bound on listed files so a pathological plugin (e.g. a whole-repo
# install of a large repository) cannot bloat API payloads.
_PLUGIN_FILES_LIMIT = 2000


class PluginSkillSummary(BaseModel):
    """Summary of a skill bundled in a plugin."""

    name: str
    description: str | None = None


def _list_plugin_files(root: Path) -> list[str]:
    """List a plugin directory's files as sorted, POSIX, root-relative paths.

    Uses ``os.walk`` so excluded directories are pruned before being descended
    into, and symlinked directories are never followed (real plugins ship
    manifest-dir symlinks such as ``.claude-plugin -> .plugin``).
    """
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _PLUGIN_FILES_EXCLUDED_DIRS]
        for filename in filenames:
            files.append((Path(dirpath) / filename).relative_to(root).as_posix())
    files.sort()
    return files[:_PLUGIN_FILES_LIMIT]


def service_plugin_contents(
    plugin: Plugin,
) -> tuple[list[PluginSkillSummary], list[str]]:
    """Contents of an already-loaded plugin: bundled skills + file listing.

    Skills include command-derived ones (``<plugin>:<command>``), matching what
    the plugin actually contributes to a conversation.
    """
    skills = [
        PluginSkillSummary(name=skill.name, description=skill.description)
        for skill in plugin.get_all_skills()
    ]
    return skills, _list_plugin_files(Path(plugin.path))


def service_load_plugin_contents(
    plugin_dir: str | Path,
) -> tuple[list[PluginSkillSummary], list[str]] | None:
    """Load a plugin directory's contents, or None if it cannot be loaded.

    Never raises: a corrupt or vanished plugin directory must not break the
    listings that embed these contents.
    """
    try:
        plugin = Plugin.load(plugin_dir)
    except Exception as e:
        logger.warning(f"Failed to load plugin contents from {plugin_dir}: {e}")
        return None
    return service_plugin_contents(plugin)


# ---------------------------------------------------------------------------
# Plugins-only marketplace catalog
# ---------------------------------------------------------------------------

# The OpenHands extensions marketplace lists both skills and true plugins under
# its ``plugins`` array, distinguished only by the entry's source path: true
# plugins live under ``./plugins/`` while skills live under ``./skills/``. We
# filter on the raw source for this reason (NOT plugin.json presence, which
# skills carry too).
_PLUGINS_SOURCE_PREFIX = "./plugins/"
# Equivalent prefix when an entry uses a structured source object (github/url)
# carrying an explicit subpath.
_PLUGINS_SUBPATH_PREFIX = "plugins/"


class MarketplacePluginInfo(BaseModel):
    """A true plugin in the marketplace catalog, with attach coordinates."""

    name: str
    description: str | None
    source: str
    ref: str | None = None
    repo_path: str | None = None
    installed: bool
    # Local contents — populated when the entry resolves to a directory in the
    # local marketplace clone; None when contents are not locally available
    # (e.g. a structured source pointing at another repository).
    path: str | None = None
    skills: list[PluginSkillSummary] | None = None
    files: list[str] | None = None


def service_get_plugins_marketplace_catalog(
    marketplace_path: str = DEFAULT_MARKETPLACE_PATH,
    installed_dir: Path | None = None,
) -> list[MarketplacePluginInfo]:
    """Get the plugins-only marketplace catalog with installation status.

    Loads the marketplace JSON from the public extensions repository, keeps only
    true plugins (source under ``./plugins/``), and enriches each with its
    attachable coordinates and installation status.

    The shared marketplace snapshot is cached, while the ``installed`` field is
    always resolved fresh from the local FS.

    Args:
        marketplace_path: Relative path to the marketplace JSON file.
        installed_dir: Directory of installed plugins to check status against.
            Defaults to ``~/.openhands/plugins/installed/``.

    Returns:
        List of MarketplacePluginInfo with plugin details and install status.
    """
    marketplace = load_marketplace_snapshot(marketplace_path)
    entries = _plugin_catalog_entries(marketplace) if marketplace is not None else []

    # Always-fresh installed check — local FS scan, not a network call.
    installed_names = {
        p.name for p in list_installed_plugins(installed_dir=installed_dir)
    }
    return [
        entry.model_copy(update={"installed": entry.name in installed_names})
        for entry in entries
    ]


def _is_true_plugin(raw_source: object) -> bool:
    """Whether a marketplace entry's *raw* source points at a true plugin.

    Must run on the raw source BEFORE ``resolve_plugin_source`` rewrites a
    relative path into an absolute one (which drops the ``./plugins/`` prefix).
    String sources are true plugins when under ``./plugins/``; structured
    source objects when their subpath is under ``plugins/``. Skills (``./skills/``)
    are excluded.
    """
    if isinstance(raw_source, str):
        return raw_source.startswith(_PLUGINS_SOURCE_PREFIX)
    subpath = getattr(raw_source, "path", None) or ""
    return subpath.startswith(_PLUGINS_SUBPATH_PREFIX)


def _plugin_catalog_entries(
    marketplace: Marketplace,
) -> list[MarketplacePluginInfo]:
    entries: list[MarketplacePluginInfo] = []
    for plugin in marketplace.plugins:
        if not _is_true_plugin(plugin.source):
            continue
        # Resolve to attachable coordinates. For a local ./plugins/<name> entry
        # this yields an absolute path with ref/repo_path None; structured
        # github/url sources yield their ref + subpath.
        source, ref, repo_path = marketplace.resolve_plugin_source(plugin)
        entry = MarketplacePluginInfo(
            name=plugin.name,
            description=plugin.description,
            source=source,
            ref=ref,
            repo_path=repo_path,
            installed=False,
        )
        # A local ./plugins/<name> entry resolves to a directory inside the
        # cached clone, so its contents can be loaded from disk. Structured
        # github/url sources may point at other repositories — no local copy,
        # so their contents stay None.
        if ref is None and repo_path is None and Path(source).is_dir():
            contents = service_load_plugin_contents(source)
            if contents is not None:
                skills, files = contents
                entry = entry.model_copy(
                    update={
                        "path": to_posix_path(source),
                        "skills": skills,
                        "files": files,
                    }
                )
        entries.append(entry)

    return entries
