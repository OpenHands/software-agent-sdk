"""Unit tests for :class:`PromptRegistry` (``registry.py``).

Pure assembly logic, exercised with fake sections.
"""

import pytest

from openhands.sdk.context.prompts.registry import PromptRegistry
from openhands.sdk.context.prompts.section import CacheTier, PromptContext

from ._fakes import FakeSection


CTX = PromptContext()


def test_register_and_build_static_and_dynamic() -> None:
    reg = PromptRegistry()
    reg.register(FakeSection("a", CacheTier.STATIC, "AAA"))
    reg.register(FakeSection("b", CacheTier.DYNAMIC, "BBB"))
    static, dynamic = reg.build(CTX)
    assert static == "AAA"
    assert dynamic == "BBB"


def test_static_sections_joined_in_registration_order() -> None:
    reg = PromptRegistry()
    reg.register(FakeSection("a", CacheTier.STATIC, "first"))
    reg.register(FakeSection("b", CacheTier.STATIC, "second"))
    static, dynamic = reg.build(CTX)
    assert static == "first\n\nsecond"
    assert dynamic is None


def test_build_groups_by_tier_preserving_order() -> None:
    """Interleaved registration still groups per tier, order preserved."""
    reg = PromptRegistry()
    reg.register(FakeSection("s1", CacheTier.STATIC, "S1"))
    reg.register(FakeSection("d1", CacheTier.DYNAMIC, "D1"))
    reg.register(FakeSection("s2", CacheTier.STATIC, "S2"))
    reg.register(FakeSection("d2", CacheTier.DYNAMIC, "D2"))
    static, dynamic = reg.build(CTX)
    assert static == "S1\n\nS2"
    assert dynamic == "D1\n\nD2"


def test_duplicate_register_raises() -> None:
    reg = PromptRegistry()
    reg.register(FakeSection("dup", CacheTier.STATIC, "x"))
    with pytest.raises(ValueError, match="already registered"):
        reg.register(FakeSection("dup", CacheTier.DYNAMIC, "y"))


def test_replace_overrides_in_place() -> None:
    reg = PromptRegistry()
    reg.register(FakeSection("a", CacheTier.STATIC, "old"))
    reg.register(FakeSection("b", CacheTier.STATIC, "b"))
    reg.replace(FakeSection("a", CacheTier.STATIC, "new"))
    static, _ = reg.build(CTX)
    # content overridden, original position preserved
    assert static == "new\n\nb"


def test_replace_adds_when_absent() -> None:
    reg = PromptRegistry()
    reg.replace(FakeSection("a", CacheTier.STATIC, "a"))
    static, _ = reg.build(CTX)
    assert static == "a"


def test_guard_false_excludes_section() -> None:
    reg = PromptRegistry()
    reg.register(FakeSection("on", CacheTier.STATIC, "on"))
    reg.register(FakeSection("off", CacheTier.STATIC, "off", enabled=False))
    static, _ = reg.build(CTX)
    assert static == "on"


def test_none_and_blank_renders_excluded() -> None:
    reg = PromptRegistry()
    reg.register(FakeSection("none", CacheTier.STATIC, None))
    reg.register(FakeSection("blank", CacheTier.STATIC, "   "))
    reg.register(FakeSection("real", CacheTier.STATIC, "real"))
    static, dynamic = reg.build(CTX)
    assert static == "real"
    assert dynamic is None


def test_empty_registry_builds_empty_static_and_none_dynamic() -> None:
    static, dynamic = PromptRegistry().build(CTX)
    assert static == ""
    assert dynamic is None


def test_render_output_is_stripped() -> None:
    reg = PromptRegistry()
    reg.register(FakeSection("a", CacheTier.STATIC, "  padded  "))
    static, _ = reg.build(CTX)
    assert static == "padded"


def test_build_threads_ctx_to_sections() -> None:
    """guard and render receive the ctx passed to build()."""

    class _BrowserGated:
        name = "browser"
        cache_tier = CacheTier.STATIC

        def guard(self, ctx: PromptContext) -> bool:
            return ctx.enable_browser

        def render(self, ctx: PromptContext) -> str | None:
            return "BROWSER"

    reg = PromptRegistry()
    reg.register(_BrowserGated())
    on = PromptContext(template_kwargs={"enable_browser": True})
    off = PromptContext(template_kwargs={"enable_browser": False})
    assert reg.build(on).static == "BROWSER"
    assert reg.build(off).static == ""
