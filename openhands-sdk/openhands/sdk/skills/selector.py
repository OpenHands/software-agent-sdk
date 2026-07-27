from __future__ import annotations

import re
from dataclasses import dataclass

from openhands.sdk.skills.catalog import SkillCatalog, SkillCatalogEntry


@dataclass(frozen=True)
class SkillSelection:
    candidate_entries: list[SkillCatalogEntry]
    query_truncated: bool = False


class DeterministicSkillSelector:
    """Lightweight lexical selector for skill invocation."""

    def select(
        self, query: str, catalog: SkillCatalog, limit: int = 5
    ) -> SkillSelection:
        q = query.strip().lower()
        if not q:
            return SkillSelection(candidate_entries=[])

        exact = [entry for entry in catalog.enabled() if entry.name.lower() == q]
        if exact:
            return SkillSelection(candidate_entries=exact[:limit])

        tokens = [t for t in re.split(r"\W+", q) if t]
        scored: list[tuple[int, SkillCatalogEntry]] = []
        for entry in catalog.enabled():
            haystack = " ".join(
                filter(
                    None,
                    [
                        entry.name,
                        entry.short_description or "",
                        entry.main_prompt,
                    ],
                )
            ).lower()
            score = sum(1 for t in tokens if t in haystack)
            if score:
                scored.append((score, entry))

        scored.sort(key=lambda item: (-item[0], item[1].name))
        return SkillSelection(candidate_entries=[entry for _, entry in scored[:limit]])
