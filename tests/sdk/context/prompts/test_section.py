"""Unit tests for the prompt-registry typed core (``section.py``).

These exercise the pure data types only -- no agent, no I/O.
"""

import sys

import pytest
from pydantic import ValidationError

from openhands.sdk.context.prompts.section import (
    CacheTier,
    Platform,
    PromptContext,
    PromptSection,
)

from ._fakes import FakeSection


def test_cache_tier_values() -> None:
    assert CacheTier.STATIC.value == "static"
    assert CacheTier.DYNAMIC.value == "dynamic"
    assert set(CacheTier) == {CacheTier.STATIC, CacheTier.DYNAMIC}


@pytest.mark.parametrize(
    "platform_str, expected",
    [
        ("win32", Platform.WINDOWS),
        ("darwin", Platform.MACOS),
        ("linux", Platform.LINUX),
        ("linux2", Platform.LINUX),
        ("freebsd13", Platform.OTHER),
    ],
)
def test_platform_current(monkeypatch, platform_str, expected) -> None:
    monkeypatch.setattr(sys, "platform", platform_str)
    assert Platform.current() is expected


def test_prompt_context_defaults() -> None:
    ctx = PromptContext()
    assert ctx.template_kwargs == {}
    assert ctx.tool_names == ()
    assert ctx.working_dir is None
    assert ctx.now is None
    assert ctx.skill_names == ()
    assert ctx.secret_names == ()
    assert isinstance(ctx.platform, Platform)
    # typed views over template_kwargs
    assert ctx.enable_browser is False
    assert ctx.model_family is None
    assert ctx.cli_mode is False


def test_prompt_context_is_frozen() -> None:
    ctx = PromptContext(working_dir="/repo")
    with pytest.raises(ValidationError):
        ctx.working_dir = "/other"  # type: ignore[misc]


def test_typed_views_read_template_kwargs() -> None:
    ctx = PromptContext(
        template_kwargs={
            "enable_browser": True,
            "model_family": "anthropic_claude",
            "cli_mode": True,
        }
    )
    assert ctx.enable_browser is True
    assert ctx.model_family == "anthropic_claude"
    assert ctx.cli_mode is True


def test_model_family_non_string_is_none() -> None:
    assert PromptContext(template_kwargs={"model_family": 123}).model_family is None


def test_fake_section_satisfies_protocol() -> None:
    section = FakeSection(name="stub", cache_tier=CacheTier.STATIC, text="hello")
    assert isinstance(section, PromptSection)
    ctx = PromptContext()
    assert section.guard(ctx) is True
    assert section.render(ctx) == "hello"
