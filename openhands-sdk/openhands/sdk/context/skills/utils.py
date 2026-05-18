"""Backward-compatible re-exports. Canonical location: openhands.sdk.skills.utils"""
from openhands.sdk.skills.utils import (  # noqa: F401
    get_skills_cache_dir,
    update_skills_repository,
)

__all__ = [
    "get_skills_cache_dir",
    "update_skills_repository",
]
