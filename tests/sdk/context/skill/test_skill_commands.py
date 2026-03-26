"""Tests for shell command execution in skill frontmatter."""

from collections.abc import Callable
from pathlib import Path

import pytest

from openhands.sdk.context.skills import CommandSpec, Skill, SkillValidationError
from openhands.sdk.context.skills.exceptions import SkillError
from openhands.sdk.context.skills.execute import (
    _execute_command,
    render_content_with_commands,
)


def test_command_spec_defaults():
    """CommandSpec should have sensible defaults."""
    spec = CommandSpec(name="test", command="echo hello")
    assert spec.timeout == 10.0 and spec.on_error == "message"


@pytest.mark.parametrize(
    ("command", "on_error", "timeout", "check"),
    [
        ("echo hello", "message", 10.0, lambda r: r == "hello"),
        ("exit 1", "message", 10.0, lambda r: "[Error:" in r),
        ("exit 1", "empty", 10.0, lambda r: r == ""),
        ("sleep 5", "message", 0.1, lambda r: "timed out" in r),
    ],
)
def test_execute_command(
    command: str, on_error: str, timeout: float, check: Callable[[str], bool]
):
    """_execute_command handles success, failure, and timeout correctly."""
    spec = CommandSpec(name="t", command=command, on_error=on_error, timeout=timeout)  # type: ignore[arg-type]
    assert check(_execute_command(spec))


def test_execute_command_failure_raises():
    """on_error='fail' should raise SkillError."""
    with pytest.raises(SkillError):
        _execute_command(CommandSpec(name="fail", command="exit 1", on_error="fail"))


@pytest.mark.parametrize(
    ("content", "commands", "extra_vars", "expected"),
    [
        ("Hi {{x}}", [], None, "Hi {{x}}"),
        ("Hi {{x}}", [CommandSpec(name="x", command="echo world")], None, "Hi world"),
        ("Hi {{x}}", [], {"x": "world"}, "Hi world"),
        ("Hi {{x}}", [], {"y": "z"}, "Hi {{x}}"),
    ],
)
def test_render_content(
    content: str, commands: list, extra_vars: dict | None, expected: str
):
    """render_content_with_commands substitutes variables correctly."""
    assert (
        render_content_with_commands(content, commands, extra_vars=extra_vars)
        == expected
    )


def test_skill_load_with_commands(tmp_path: Path):
    """Skill.load parses commands from frontmatter."""
    skill_md = tmp_path / "test-skill" / "SKILL.md"
    skill_md.parent.mkdir()
    skill_md.write_text(
        "---\nname: test-skill\ncommands:\n"
        "  - name: a\n    command: echo A\n"
        "  - name: b\n    command: echo B\n    timeout: 5.0\n---\n{{a}} {{b}}"
    )
    skill = Skill.load(skill_md)
    assert len(skill.commands) == 2 and skill.commands[1].timeout == 5.0


def test_skill_load_commands_validation(tmp_path: Path):
    """Skill.load rejects invalid commands field."""
    path = tmp_path / "bad.md"
    path.write_text("---\nname: s\ncommands: not-a-list\n---\n#")
    with pytest.raises(SkillValidationError, match="commands must be a list"):
        Skill.load(path)


@pytest.mark.parametrize(
    ("content", "commands", "expected"),
    [
        ("Hello", [], "Hello"),
        ("Out: {{x}}", [CommandSpec(name="x", command="echo hi")], "Out: hi"),
    ],
)
def test_skill_render_content(content: str, commands: list, expected: str):
    """Skill.render_content executes commands and substitutes."""
    assert (
        Skill(name="t", content=content, commands=commands).render_content() == expected
    )
