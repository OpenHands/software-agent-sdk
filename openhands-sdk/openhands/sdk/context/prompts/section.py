"""Typed core for the system-prompt section registry.

Pure data types -- no I/O -- so they unit-test in isolation.
"""

import sys
from collections.abc import Mapping
from enum import StrEnum
from typing import NamedTuple, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


__all__ = [
    "CacheTier",
    "Platform",
    "PromptBlocks",
    "PromptContext",
    "PromptSection",
]


class CacheTier(StrEnum):
    """Block a section renders into; maps 1:1 onto ``SystemPromptEvent``'s two
    content blocks. ``STATIC`` is cache-stable across conversations, ``DYNAMIC``
    is per-conversation."""

    STATIC = "static"
    DYNAMIC = "dynamic"


class Platform(StrEnum):
    """Host platform, derived from :data:`sys.platform`."""

    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"
    OTHER = "other"

    @classmethod
    def current(cls) -> "Platform":
        if sys.platform == "win32":
            return cls.WINDOWS
        if sys.platform == "darwin":
            return cls.MACOS
        if sys.platform.startswith("linux"):
            return cls.LINUX
        return cls.OTHER


class PromptContext(BaseModel):
    """Frozen snapshot of everything that shapes a system prompt.

    ``template_kwargs`` is the resolved kwarg dict for the static, cache-stable
    block; the other fields snapshot per-conversation signals. ``enable_browser``
    / ``model_family`` / ``cli_mode`` are typed views over ``template_kwargs``.
    """

    model_config = ConfigDict(frozen=True)

    template_kwargs: Mapping[str, object] = Field(default_factory=dict)
    tool_names: tuple[str, ...] = Field(default_factory=tuple)
    platform: Platform = Field(default_factory=Platform.current)
    working_dir: str | None = None
    now: str | None = None
    skill_names: tuple[str, ...] = Field(default_factory=tuple)
    secret_names: tuple[str, ...] = Field(default_factory=tuple)

    @property
    def enable_browser(self) -> bool:
        return bool(self.template_kwargs.get("enable_browser", False))

    @property
    def model_family(self) -> str | None:
        value = self.template_kwargs.get("model_family")
        return value if isinstance(value, str) else None

    @property
    def cli_mode(self) -> bool:
        return bool(self.template_kwargs.get("cli_mode", False))


@runtime_checkable
class PromptSection(Protocol):
    """One pure unit of system-prompt text -- a guarded block that contributes
    text only when it applies.

    Assembled in two steps against the frozen :class:`PromptContext`:
    :meth:`guard` decides whether the section applies (if ``False`` it is
    skipped and :meth:`render` never runs), then :meth:`render` produces the
    text. The split is intentional -- ``guard`` returning ``False`` means "not
    applicable here" (e.g. browser disabled, wrong platform), while ``render``
    returning ``None`` means "applicable, but nothing to add right now" (e.g. no
    skills present).

    Both must be pure -- read-only on ``ctx``, no I/O -- so sections are
    testable in isolation. ``name`` is the registry's unique key for
    dedup/override; ``cache_tier`` selects the static or dynamic block.
    """

    name: str
    cache_tier: CacheTier

    def guard(self, ctx: PromptContext) -> bool:
        """Return ``True`` if this section applies to ``ctx``."""
        ...

    def render(self, ctx: PromptContext) -> str | None:
        """Return the section's text, or ``None``/blank to contribute nothing."""
        ...


class PromptBlocks(NamedTuple):
    """The two content blocks a registry assembles.

    Maps 1:1 onto ``SystemPromptEvent``: ``static`` becomes the cache-stable
    ``system_prompt`` block and ``dynamic`` the optional ``dynamic_context``
    (``None`` when no dynamic section produced output). It is a tuple, so it
    still unpacks as ``static, dynamic = registry.build(ctx)``.
    """

    static: str
    dynamic: str | None = None
