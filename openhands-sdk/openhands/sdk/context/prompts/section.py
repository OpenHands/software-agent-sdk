"""Typed core for the system-prompt section registry.

Pure data types -- no I/O -- so they unit-test in isolation.
"""

import sys
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import NamedTuple, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator


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

    template_kwargs: Mapping[str, object] = Field(
        default_factory=dict, validate_default=True
    )
    tool_names: tuple[str, ...] = Field(default_factory=tuple)
    platform: Platform = Field(default_factory=Platform.current)
    working_dir: str | None = None
    now: str | None = None
    skill_names: tuple[str, ...] = Field(default_factory=tuple)
    secret_names: tuple[str, ...] = Field(default_factory=tuple)
    # Resolved dynamic-tier data (skills gated + secrets merged before assembly),
    # consumed by the dynamic sections. ``repo_skills`` is (name, content) pairs;
    # ``secret_infos`` is (name, description) pairs.
    repo_skills: tuple[tuple[str, str], ...] = Field(default_factory=tuple)
    available_skills_prompt: str | None = None
    custom_suffix: str | None = None
    secret_infos: tuple[tuple[str, str | None], ...] = Field(default_factory=tuple)

    @field_validator("template_kwargs", mode="after")
    @classmethod
    def _freeze_template_kwargs(
        cls, value: Mapping[str, object]
    ) -> Mapping[str, object]:
        # frozen=True blocks attribute reassignment but not nested mutation;
        # store a read-only copy so the snapshot and its views cannot drift.
        return MappingProxyType(dict(value))

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
