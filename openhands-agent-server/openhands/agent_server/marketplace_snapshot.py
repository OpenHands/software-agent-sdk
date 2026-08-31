"""Shared loading and caching for the public extensions marketplace."""

import json
from pathlib import Path
from threading import Lock
from time import monotonic

from pydantic import ValidationError

from openhands.sdk.logger import get_logger
from openhands.sdk.marketplace import Marketplace
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

_MARKETPLACE_TTL_SECONDS = 300
_marketplace_cache: dict[str, tuple[float, Marketplace]] = {}
_marketplace_cache_lock = Lock()


def load_marketplace_snapshot(
    marketplace_path: str = DEFAULT_MARKETPLACE_PATH,
) -> Marketplace | None:
    """Return a successfully loaded marketplace, reusing it within the TTL."""
    now = monotonic()
    cached = _marketplace_cache.get(marketplace_path)
    if cached is not None and now - cached[0] < _MARKETPLACE_TTL_SECONDS:
        return cached[1]

    with _marketplace_cache_lock:
        now = monotonic()
        cached = _marketplace_cache.get(marketplace_path)
        if cached is not None and now - cached[0] < _MARKETPLACE_TTL_SECONDS:
            return cached[1]

        cache_dir = get_skills_cache_dir()
        repo_path = update_skills_repository(
            PUBLIC_SKILLS_REPO, PUBLIC_SKILLS_REF, cache_dir
        )
        if repo_path is None:
            logger.warning("Failed to access public extensions repository")
            return None

        marketplace = _load_marketplace(repo_path, marketplace_path)
        if marketplace is not None:
            _marketplace_cache[marketplace_path] = (monotonic(), marketplace)
        return marketplace


def _load_marketplace(repo_path: Path, marketplace_path: str) -> Marketplace | None:
    try:
        return Marketplace.load(repo_path)
    except (FileNotFoundError, ValueError) as discovery_error:
        marketplace_file = repo_path / marketplace_path
        if not marketplace_file.exists():
            logger.warning(
                f"Failed to load marketplace via manifest discovery "
                f"({discovery_error}); fallback file not found: {marketplace_file}"
            )
            return None

        try:
            with open(marketplace_file, encoding="utf-8") as file:
                data = json.load(file)
            return Marketplace.model_validate(
                {**data, "path": to_posix_path(repo_path)}
            )
        except (json.JSONDecodeError, ValidationError, OSError) as fallback_error:
            logger.warning(
                f"Failed to load marketplace: {discovery_error}, {fallback_error}"
            )
            return None
