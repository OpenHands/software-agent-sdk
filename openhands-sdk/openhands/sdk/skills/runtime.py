from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from openhands.sdk.skills.catalog import (
    SkillCatalog,
    SkillCatalogEntry,
    SkillCatalogError,
    SkillResourceHandle,
    build_skill_catalog,
)
from openhands.sdk.skills.selector import DeterministicSkillSelector, SkillSelection
from openhands.sdk.skills.skill import Skill


@dataclass(frozen=True)
class SkillReadResult:
    entry: SkillCatalogEntry
    handle: SkillResourceHandle


class SkillRuntime:
    """Small Codex-style runtime facade for skill discovery and reading."""

    def __init__(self, skills: Iterable[Skill], *, authority: str = "host") -> None:
        self.catalog: SkillCatalog = build_skill_catalog(skills, authority=authority)
        self.selector = DeterministicSkillSelector()

    def list(self) -> list[SkillCatalogEntry]:
        return self.catalog.enabled()

    def select(self, query: str, limit: int = 5) -> SkillSelection:
        return self.selector.select(query, self.catalog, limit=limit)

    def read(self, skill_name: str, relative_path: str = "SKILL.md") -> SkillReadResult:
        entry = self.catalog.get(skill_name)
        if entry is None:
            raise SkillCatalogError(f"unknown skill: {skill_name}")
        if entry.resource_root is None:
            raise SkillCatalogError(f"skill {skill_name} has no resource root")

        skill_root = Path(entry.resource_root).expanduser().resolve()
        candidate = (skill_root / relative_path).resolve()
        if skill_root not in candidate.parents and candidate != skill_root:
            raise SkillCatalogError(f"path escapes skill root: {relative_path}")
        if not candidate.is_file():
            raise SkillCatalogError(relative_path)

        handle = SkillResourceHandle(
            entry=entry,
            relative_path=relative_path,
            absolute_path=str(candidate),
            contents=candidate.read_text(encoding="utf-8"),
        )
        return SkillReadResult(entry=entry, handle=handle)
