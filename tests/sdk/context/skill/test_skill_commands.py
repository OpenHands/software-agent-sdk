"""Tests for inline !`command` execution in skill content.

The !`command` syntax lets skill authors embed dynamic shell output in
markdown.  These tests verify:

  - Basic execution: !`echo hello` → hello
  - Error / timeout handling
  - Output truncation for large outputs
  - Code-block safety: fenced (```) and inline (`) blocks are never executed
  - Unclosed fenced blocks: an odd number of ``` delimiters must not leak
    commands that follow the last unclosed fence
  - Escape hatch: \\!`cmd` is preserved as the literal text !`cmd`
  - Integration with the Skill model (load + render)
"""

from pathlib import Path

import pytest

from openhands.sdk.context.skills import Skill
from openhands.sdk.context.skills.execute import (
    MAX_OUTPUT_SIZE,
    _execute_inline_command,
    render_content_with_commands,
)


# ---------------------------------------------------------------------------
# Low-level: _execute_inline_command
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "timeout", "check_fn"),
    [
        pytest.param("echo hello", 10.0, lambda r: r == "hello", id="success"),
        pytest.param(
            "printf 'line1\nline2\nline3'",
            10.0,
            lambda r: r == "line1\nline2\nline3",
            id="multiline_output",
        ),
        pytest.param("exit 1", 10.0, lambda r: "[Error:" in r, id="failure"),
        pytest.param("sleep 5", 0.1, lambda r: "timed out" in r, id="timeout"),
    ],
)
def test_execute_inline_command(command, timeout, check_fn):
    assert check_fn(_execute_inline_command(command, timeout=timeout))


def test_execute_inline_command_respects_working_dir(tmp_path: Path):
    result = _execute_inline_command("pwd", working_dir=tmp_path)
    assert result == str(tmp_path.resolve())


def test_execute_inline_command_truncates_large_output():
    size = MAX_OUTPUT_SIZE + 100
    result = _execute_inline_command(f"python3 -c \"print('x' * {size})\"")
    assert result.endswith("... [output truncated]")
    assert len(result.encode()) <= MAX_OUTPUT_SIZE + 50  # small overhead ok


# ---------------------------------------------------------------------------
# Rendering: basic command substitution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param("Hello world", "Hello world", id="plain_text_unchanged"),
        pytest.param("Branch: !`echo main`", "Branch: main", id="single_command"),
        pytest.param(
            "A: !`echo one` B: !`echo two`", "A: one B: two", id="multiple_commands"
        ),
        pytest.param("!``", "!``", id="empty_backticks_ignored"),
    ],
)
def test_render_basic(content, expected):
    assert render_content_with_commands(content) == expected


# ---------------------------------------------------------------------------
# Rendering: code blocks are never executed
# ---------------------------------------------------------------------------


def test_render_preserves_inline_code():
    """Regular `code` spans are left alone."""
    content = "Use `git status` to check"
    assert render_content_with_commands(content) == content


def test_render_preserves_fenced_block():
    """Commands inside ``` fences are not executed."""
    content = "Real: !`echo yes`\n```\n!`echo no`\n```"
    result = render_content_with_commands(content)
    assert "yes" in result
    assert "!`echo no`" in result


def test_render_inline_code_next_to_command():
    """`code` immediately followed by a real !`cmd` — both handled correctly."""
    content = "Run `git status` then !`echo done`"
    result = render_content_with_commands(content)
    assert "`git status`" in result
    assert "done" in result


# ---------------------------------------------------------------------------
# Rendering: unclosed fenced blocks
#
# When a fenced block is opened but never closed (odd number of ```),
# everything after the opening ``` must be treated as inside the fence —
# no commands should be executed there.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "executed", "preserved"),
    [
        pytest.param(
            "```\nblock1\n```\n!`echo mid`\n```\n!`echo sneaky`\n",
            "mid",
            "!`echo sneaky`",
            id="odd_fences_protects_trailing_command",
        ),
        pytest.param(
            "```\n!`echo nope`\n",
            None,
            "!`echo nope`",
            id="single_unclosed_fence",
        ),
    ],
)
def test_render_unclosed_fenced_blocks(content, executed, preserved):
    result = render_content_with_commands(content)
    if executed is not None:
        assert executed in result
    assert preserved in result


def test_render_properly_closed_fences():
    content = "```\nblock1\n```\n!`echo between`\n```\nblock2\n```"
    result = render_content_with_commands(content)
    assert "between" in result
    assert "!`echo between`" not in result


# ---------------------------------------------------------------------------
# Rendering: escape hatch — \!`cmd` produces the literal text !`cmd`
#
# This lets skill authors document the !`...` syntax itself, or show
# examples of commands without them being run at render time.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "expected_literal", "expected_executed"),
    [
        pytest.param(
            r"\!`echo hello`",
            "!`echo hello`",
            None,
            id="escaped_becomes_literal",
        ),
        pytest.param(
            r"Docs: \!`echo no` Real: !`echo yes`",
            "!`echo no`",
            "yes",
            id="escaped_and_real_coexist",
        ),
    ],
)
def test_render_escaped_commands(content, expected_literal, expected_executed):
    result = render_content_with_commands(content)
    assert expected_literal in result
    if expected_executed is not None:
        assert expected_executed in result


def test_render_escape_inside_fenced_block_untouched():
    r"""\\!`cmd` inside a fenced block is left completely as-is."""
    content = "```\n\\!`echo hi`\n```"
    result = render_content_with_commands(content)
    assert result == content


# ---------------------------------------------------------------------------
# Integration: Skill.render_content
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param("Plain text", "Plain text", id="no_commands"),
        pytest.param("Out: !`echo hi`", "Out: hi", id="with_command"),
    ],
)
def test_skill_render_content(content, expected):
    assert Skill(name="t", content=content).render_content() == expected


def test_skill_load_and_render(tmp_path: Path):
    skill_md = tmp_path / "test-skill" / "SKILL.md"
    skill_md.parent.mkdir()
    skill_md.write_text("---\nname: test-skill\n---\nBranch: !`echo main`\n")
    skill = Skill.load(skill_md)
    assert skill.render_content() == "Branch: main"
