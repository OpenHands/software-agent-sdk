from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from openhands.sdk.skills.skill import Skill


@dataclass(frozen=True)
class SkillCatalogEntry:
    """Structured skill record used for selection and routing."""

    name: str
    skill: Skill
    authority: str = "host"
    package_id: str | None = None
    display_path: str | None = None
    enabled: bool = True
    prompt_visible: bool = True
    dependencies: tuple[str, ...] = ()

    @property
    def main_prompt(self) -> str:
        return self.skill.content

    @property
    def short_description(self) -> str | None:
        return self.skill.description

    @property
    def source(self) -> str | None:
        return self.skill.source

    @property
    def resource_root(self) -> str | None:
        if self.skill.resources is None:
            return None
        return self.skill.resources.skill_root


@dataclass
class SkillCatalog:
    entries: list[SkillCatalogEntry] = field(default_factory=list)

    def extend(self, entries: Iterable[SkillCatalogEntry]) -> None:
        for entry in entries:
            self.add(entry)

    def add(self, entry: SkillCatalogEntry) -> None:
        if any(
            existing.name == entry.name and existing.authority == entry.authority
            for existing in self.entries
        ):
            return
        self.entries.append(entry)

    def get(self, name: str) -> SkillCatalogEntry | None:
        return next(
            (entry for entry in self.entries if entry.enabled and entry.name == name),
            None,
        )

    def enabled(self) -> list[SkillCatalogEntry]:
        return [entry for entry in self.entries if entry.enabled]


@dataclass(frozen=True)
class SkillResourceHandle:
    entry: SkillCatalogEntry
    relative_path: str
    absolute_path: str
    contents: str


class SkillCatalogError(RuntimeError):
    pass


def build_skill_catalog(
    skills: Iterable[Skill], *, authority: str = "host"
) -> SkillCatalog:
    catalog = SkillCatalog()
    for skill in skills:
        catalog.add(
            SkillCatalogEntry(
                name=skill.name,
                skill=skill,
                authority=authority,
                display_path=skill.source,
                enabled=True,
                prompt_visible=not skill.disable_model_invocation,
            )
        )
    return catalog
