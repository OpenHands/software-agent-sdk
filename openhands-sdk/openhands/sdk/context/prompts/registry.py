"""Assembles registered :class:`PromptSection` units into the ``(static,
dynamic)`` pair consumed by ``SystemPromptEvent``.

Deliberately not an agent field: agents keep their existing
prompt-customization surface; a module-level default registry is the override
point.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Final

from openhands.sdk.context.prompts.section import (
    CacheTier,
    PromptBlocks,
    PromptContext,
    PromptSection,
)


__all__ = ["PromptRegistry"]

_SECTION_SEPARATOR: Final[str] = "\n\n"


@dataclass(slots=True, eq=False)
class PromptRegistry:
    """Ordered collection of prompt sections, assembled in registration order."""

    _sections: dict[str, PromptSection] = field(default_factory=dict, init=False)

    def register(self, section: PromptSection) -> None:
        """Add a new section; raise on a duplicate name (use :meth:`replace`)."""
        if section.name in self._sections:
            raise ValueError(
                f"A prompt section named {section.name!r} is already registered; "
                "use replace() to override it."
            )
        self._sections[section.name] = section

    def replace(self, section: PromptSection) -> None:
        """Add ``section``, or override a same-named one in place."""
        self._sections[section.name] = section

    def build(self, ctx: PromptContext) -> PromptBlocks:
        """Render guarded sections, grouped by ``cache_tier`` in registration
        order. ``static`` is always a string; ``dynamic`` is ``None`` when empty.
        """
        buckets: defaultdict[CacheTier, list[str]] = defaultdict(list)
        for section in self._sections.values():
            if not section.guard(ctx):
                continue
            text = section.render(ctx)
            if text is None:
                continue
            text = text.strip()
            if not text:
                continue
            buckets[section.cache_tier].append(text)

        return PromptBlocks(
            static=_SECTION_SEPARATOR.join(buckets[CacheTier.STATIC]),
            dynamic=_SECTION_SEPARATOR.join(buckets[CacheTier.DYNAMIC]) or None,
        )
