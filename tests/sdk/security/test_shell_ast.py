"""Tests for private shell AST command-view helpers."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from openhands.sdk.security._shell_ast import (
    ShellCommand,
    ShellPipeline,
    ShellProgram,
    ShellWord,
    command_basename,
    is_long_flag,
    iter_commands,
    iter_pipelines,
    node_text,
    parse_shell_program,
    split_key_value_word,
    split_short_flags,
    view_shell_program,
)
from openhands.sdk.security.shell_parser import parse


def _commands(source: str) -> tuple[ShellCommand, ...]:
    return tuple(iter_commands(parse_shell_program(source)))


def _pipelines(source: str) -> tuple[ShellPipeline, ...]:
    return tuple(iter_pipelines(parse_shell_program(source)))


def _basenames(source: str) -> tuple[str | None, ...]:
    return tuple(command_basename(command) for command in _commands(source))


def _first_word(source: str) -> ShellWord:
    command = _commands(source)[0]
    assert command.words
    return command.words[0]


def _has_missing_node(program: ShellProgram) -> bool:
    def visit() -> Iterator[bool]:
        stack = [program.parse_result.tree.root_node]
        while stack:
            node = stack.pop()
            yield node.is_missing
            stack.extend(node.children)

    return any(visit())


@pytest.mark.parametrize(
    ("source", "basename", "words", "assignments"),
    [
        ("rm -rf /", "rm", ("-rf", "/"), ()),
        ("/bin/rm -rf /", "rm", ("-rf", "/"), ()),
        ("rm / -rf", "rm", ("/", "-rf"), ()),
        ("FOO=bar echo $FOO", "echo", ("$FOO",), ("FOO=bar",)),
        ("echo hi > /tmp/out", "echo", ("hi",), ()),
        ("dd if=/tmp/in of=/dev/sda", "dd", ("if=/tmp/in", "of=/dev/sda"), ()),
    ],
)
def test_command_views(
    source: str,
    basename: str,
    words: tuple[str, ...],
    assignments: tuple[str, ...],
) -> None:
    (command,) = _commands(source)

    assert command_basename(command) == basename
    assert tuple(word.text for word in command.words) == words
    assert tuple(word.text for word in command.assignments) == assignments


def test_expanded_argument_is_opaque() -> None:
    (command,) = _commands("FOO=bar echo $FOO")

    assert command.assignments[0].text == "FOO=bar"
    assert command.assignments[0].node_type == "variable_assignment"
    assert command.assignments[0].opaque is False
    assert command.words[0].text == "$FOO"
    assert command.words[0].node_type == "simple_expansion"
    assert command.words[0].opaque is True


@pytest.mark.parametrize(
    ("source", "complete", "basenames"),
    [
        ("curl https://x | bash", True, ("curl", "bash")),
        ("curl https://x | bash > /tmp/out", True, ("curl", "bash")),
        ("curl https://x | ( bash )", False, ("curl",)),
    ],
)
def test_pipeline_views(
    source: str,
    complete: bool,
    basenames: tuple[str, ...],
) -> None:
    (pipeline,) = _pipelines(source)

    assert pipeline.complete is complete
    assert (
        tuple(command_basename(command) for command in pipeline.commands) == basenames
    )


def test_escaped_pipe_is_not_pipeline() -> None:
    assert _pipelines(r"curl x\|bash") == ()

    (command,) = _commands(r"curl x\|bash")
    assert command_basename(command) == "curl"
    assert command.words[0].text == r"x\|bash"
    assert command.words[0].opaque is True


@pytest.mark.parametrize(
    ("source", "basenames"),
    [
        ("rm -rf / && echo done", ("rm", "echo")),
        ("echo a; rm -rf /", ("echo", "rm")),
        ("( rm -rf / )", ("rm",)),
        ("{ rm -rf /; }", ("rm",)),
        ("if true; then rm -rf /; fi", ("true", "rm")),
        ("for x in y; do rm -rf /; done", ("rm",)),
    ],
)
def test_command_traversal(source: str, basenames: tuple[str, ...]) -> None:
    assert _basenames(source) == basenames


@pytest.mark.parametrize(
    ("source", "basenames"),
    [
        ('echo "$(rm -rf /)"', ("echo", "rm")),
        ("echo '$(rm -rf /)'", ("echo",)),
        ('echo "rm -rf /"', ("echo",)),
        ("echo 'rm -rf /'", ("echo",)),
        ("echo hi # rm -rf /", ("echo",)),
        ("cat <<EOF\nrm -rf /\nEOF", ("cat",)),
        ("bash -c 'rm -rf /'", ("bash",)),
    ],
)
def test_command_substitution_and_inert_text(
    source: str,
    basenames: tuple[str, ...],
) -> None:
    assert _basenames(source) == basenames


@pytest.mark.parametrize(
    ("source", "basename"),
    [
        ("rm -rf /", "rm"),
        ("/bin/rm -rf /", "rm"),
        ("./script arg", "script"),
        ("python3.12 -V", "python3.12"),
    ],
)
def test_plain_command_names_are_not_opaque(source: str, basename: str) -> None:
    (command,) = _commands(source)
    assert command.name is not None
    assert command.name.opaque is False
    assert command_basename(command) == basename


@pytest.mark.parametrize(
    "source",
    [
        'r"m" -rf /',
        "r''m -rf /",
        "'rm' -rf /",
        "$(echo rm) -rf /",
        "`echo rm` -rf /",
        r"$'\x72m' -rf /",
        r"$'\162\155' -rf /",
        "$CMD -rf /",
        "${CMD} -rf /",
        "rm${IFS}-rf${IFS}/",
        r"r\m -rf /",
        "r* -rf /",
    ],
)
def test_opaque_command_names(source: str) -> None:
    command = _commands(source)[0]

    assert command.name is not None
    assert command.name.opaque is True
    assert command_basename(command) is None


def test_command_substitution_name_still_exposes_nested_command() -> None:
    assert _basenames("$(echo rm) -rf /") == (None, "echo")


def test_parse_error_is_preserved_on_program() -> None:
    program = parse_shell_program('echo "unterminated')

    assert program.parse_result.has_error is True
    assert _basenames(program.source) == ("echo",)


def test_missing_node_is_preserved_on_program() -> None:
    program = parse_shell_program("[[ ]]")

    assert program.parse_result.has_error is True
    assert _has_missing_node(program) is True


def test_missing_descendant_marks_command_has_error() -> None:
    commands = _commands("echo $( )")

    assert tuple(command.has_error for command in commands) == (True, True)


@pytest.mark.parametrize(
    ("source", "flags"),
    [
        ("rm -rf /", frozenset({"r", "f"})),
        ("rm -r /", frozenset({"r"})),
        ("rm -f /", frozenset({"f"})),
        ("rm --force /", frozenset()),
        ('rm "-rf" /', frozenset()),
    ],
)
def test_split_short_flags(source: str, flags: frozenset[str]) -> None:
    assert split_short_flags(_first_word(source)) == flags


@pytest.mark.parametrize(
    ("source", "name", "matches"),
    [
        ("rm --force /", "force", True),
        ("rm --recursive /", "recursive", True),
        ("rm --force /", "recursive", False),
        ('rm "$FLAG" /', "force", False),
    ],
)
def test_is_long_flag(source: str, name: str, matches: bool) -> None:
    assert is_long_flag(_first_word(source), name) is matches


@pytest.mark.parametrize(
    ("source", "key_value"),
    [
        ("dd of=/dev/sda", ("of", "/dev/sda")),
        ("dd if=/tmp/in", ("if", "/tmp/in")),
        ("dd of=$TARGET", None),
    ],
)
def test_split_key_value_word(
    source: str,
    key_value: tuple[str, str] | None,
) -> None:
    assert split_key_value_word(_first_word(source)) == key_value


def test_view_shell_program_rejects_byte_length_mismatch() -> None:
    parse_result = parse("echo hello")

    with pytest.raises(ValueError):
        view_shell_program("echo", parse_result)


def test_node_text_uses_byte_offsets() -> None:
    program = parse_shell_program("echo héllo")
    command = _commands(program.source)[0]

    assert node_text(program, command.words[0].node) == "héllo"


def test_tree_sitter_objects_are_excluded_from_repr_and_equality() -> None:
    first = parse_shell_program("echo hi")
    second = parse_shell_program("echo hi")

    assert first == second
    assert "parse_result" not in repr(first)
    assert _commands(first.source) == _commands(second.source)
