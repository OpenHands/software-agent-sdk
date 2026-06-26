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

import json
from pathlib import Path
from time import monotonic

from pydantic import BaseModel, ValidationError

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
from openhands.sdk.skills.skill import (
    DEFAULT_MARKETPLACE_PATH,
    PUBLIC_SKILLS_REF,
    PUBLIC_SKILLS_REPO,
)
from openhands.sdk.skills.utils import (
    get_skills_cache_dir,
    update_skills_repository,
)
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


# ---------------------------------------------------------------------------
# Marketplace catalog cache
# ---------------------------------------------------------------------------
# Mirrors the skills marketplace cache: each call would otherwise trigger a git
# fetch (network-bound, multiple seconds). A short TTL avoids that on every tab
# open. Only the catalog structure is cached; ``installed`` is always derived
# fresh from the local FS. Unlike the skills cache, an *empty* result (e.g. a
# transient fetch failure) is NOT cached, so one flaky fetch does not blank the
# catalog for the whole TTL.
#
# Type: (timestamp, list-of-(name, description, source, ref, repo_path)) or None
_PluginCatalogEntry = tuple[str, str | None, str, str | None, str | None]
_plugin_catalog_cache: tuple[float, list[_PluginCatalogEntry]] | None = None
_PLUGIN_CATALOG_TTL_SECONDS = 300  # 5 minutes


def service_get_plugins_marketplace_catalog(
    marketplace_path: str = DEFAULT_MARKETPLACE_PATH,
    installed_dir: Path | None = None,
) -> list[MarketplacePluginInfo]:
    """Get the plugins-only marketplace catalog with installation status.

    Loads the marketplace JSON from the public extensions repository, keeps only
    true plugins (source under ``./plugins/``), and enriches each with its
    attachable coordinates and installation status.

    The catalog structure is cached for ``_PLUGIN_CATALOG_TTL_SECONDS`` to avoid
    a git fetch on every call. The ``installed`` field is always resolved fresh
    from the local FS.

    Args:
        marketplace_path: Relative path to the marketplace JSON file.
        installed_dir: Directory of installed plugins to check status against.
            Defaults to ``~/.openhands/plugins/installed/``.

    Returns:
        List of MarketplacePluginInfo with plugin details and install status.
    """
    global _plugin_catalog_cache

    now = monotonic()
    if (
        _plugin_catalog_cache is not None
        and now - _plugin_catalog_cache[0] < _PLUGIN_CATALOG_TTL_SECONDS
    ):
        entries = _plugin_catalog_cache[1]
    else:
        entries = _fetch_plugin_catalog_entries(marketplace_path)
        # Only cache non-empty results so a transient fetch failure does not
        # blank the catalog for the whole TTL.
        if entries:
            _plugin_catalog_cache = (now, entries)

    # Always-fresh installed check — local FS scan, not a network call.
    installed_names = {
        p.name for p in list_installed_plugins(installed_dir=installed_dir)
    }
    return [
        MarketplacePluginInfo(
            name=name,
            description=desc,
            source=src,
            ref=ref,
            repo_path=repo_path,
            installed=name in installed_names,
        )
        for name, desc, src, ref, repo_path in entries
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


def _fetch_plugin_catalog_entries(marketplace_path: str) -> list[_PluginCatalogEntry]:
    """Fetch the marketplace and keep only true plugins.

    Slow path: git fetch + read the marketplace JSON. Returns
    ``(name, description, source, ref, repo_path)`` tuples, or an empty list on
    error.
    """
    cache_dir = get_skills_cache_dir()
    repo_path = update_skills_repository(
        PUBLIC_SKILLS_REPO, PUBLIC_SKILLS_REF, cache_dir
    )

    if repo_path is None:
        logger.warning("Failed to access public extensions repository")
        return []

    # Primary loader: ``Marketplace.load`` discovers the manifest in
    # ``.plugin/`` or ``.claude-plugin/`` — the real OpenHands/extensions layout,
    # where ``.plugin/marketplace.json`` points at the published catalog. We must
    # NOT gate on ``marketplace_path`` (``marketplaces/default.json``) existing
    # first: that file is absent in the current extensions repo, so an early
    # return there would blank the catalog even though the manifest is present.
    # The explicit ``marketplace_path`` file is only a fallback for layouts that
    # ship it instead of a ``.plugin/`` manifest.
    try:
        marketplace = Marketplace.load(repo_path)
    except (FileNotFoundError, ValueError) as e:
        marketplace_file = repo_path / marketplace_path
        if not marketplace_file.exists():
            logger.warning(
                f"Failed to load marketplace via manifest discovery ({e}); "
                f"fallback file not found: {marketplace_file}"
            )
            return []
        try:
            with open(marketplace_file, encoding="utf-8") as f:
                data = json.load(f)
            marketplace = Marketplace.model_validate(
                {**data, "path": to_posix_path(repo_path)}
            )
        except (json.JSONDecodeError, ValidationError, OSError) as e2:
            logger.warning(f"Failed to load marketplace: {e}, {e2}")
            return []

    entries: list[_PluginCatalogEntry] = []
    for plugin in marketplace.plugins:
        if not _is_true_plugin(plugin.source):
            continue
        # Resolve to attachable coordinates. For a local ./plugins/<name> entry
        # this yields an absolute path with ref/repo_path None; structured
        # github/url sources yield their ref + subpath.
        source, ref, repo_path = marketplace.resolve_plugin_source(plugin)
        entries.append((plugin.name, plugin.description, source, ref, repo_path))

    return entries
