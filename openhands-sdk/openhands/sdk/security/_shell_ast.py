"""Private tree-sitter-bash command views for security analyzers."""

from __future__ import annotations

import posixpath
from collections.abc import Iterator
from dataclasses import dataclass, field

from tree_sitter import Node

from openhands.sdk.security.shell_parser import ParseResult, parse


_OPAQUE_WORD_CHARS = frozenset("\"'`\\$*?[]{}()<>|&;!~")
_COMMAND_CHILD_SKIP_TYPES = frozenset(
    {
        "command_name",
        "comment",
        "file_redirect",
        "heredoc_redirect",
        "herestring_redirect",
        "redirected_statement",
        "variable_assignment",
    }
)


@dataclass(frozen=True, slots=True)
class ShellProgram:
    source: str
    source_bytes: bytes
    parse_result: ParseResult = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ShellWord:
    text: str
    node_type: str
    opaque: bool
    node: Node = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ShellCommand:
    name: ShellWord | None
    words: tuple[ShellWord, ...]
    assignments: tuple[ShellWord, ...]
    node: Node = field(repr=False, compare=False)
    has_error: bool = False


@dataclass(frozen=True, slots=True)
class ShellPipeline:
    commands: tuple[ShellCommand, ...]
    complete: bool
    node: Node = field(repr=False, compare=False)
    has_error: bool = False


def parse_shell_program(source: str) -> ShellProgram:
    """Parse ``source`` and return its private shell syntax view."""
    return view_shell_program(source, parse(source))


def view_shell_program(source: str, parse_result: ParseResult) -> ShellProgram:
    """Create a shell syntax view from a matching parse result.

    The parse tree must span the UTF-8 byte length of ``source``. If a caller
    passes a same-length source different from the parsed text, byte slicing will
    reflect that caller error; this helper intentionally does not keep a second
    copy of the parsed bytes to detect it.
    """
    source_bytes = source.encode()
    if parse_result.tree.root_node.end_byte != len(source_bytes):
        raise ValueError("parse result does not match source byte length")
    return ShellProgram(
        source=source,
        source_bytes=source_bytes,
        parse_result=parse_result,
    )


def node_text(program: ShellProgram, node: Node) -> str:
    """Return ``node`` text using tree-sitter byte offsets."""
    return program.source_bytes[node.start_byte : node.end_byte].decode()


def iter_commands(program: ShellProgram) -> Iterator[ShellCommand]:
    """Yield real ``command`` nodes from the parsed shell syntax tree."""
    for node in _iter_nodes(program.parse_result.tree.root_node):
        if node.type == "command":
            yield _view_command(program, node)


def iter_pipelines(program: ShellProgram) -> Iterator[ShellPipeline]:
    """Yield pipeline views for tree-sitter ``pipeline`` nodes."""
    for node in _iter_nodes(program.parse_result.tree.root_node):
        if node.type == "pipeline":
            yield _view_pipeline(program, node)


def command_basename(command: ShellCommand) -> str | None:
    """Return the POSIX basename for a non-opaque command name."""
    if command.name is None or command.name.opaque:
        return None
    return posixpath.basename(command.name.text)


def split_short_flags(word: ShellWord) -> frozenset[str]:
    """Split a non-opaque short flag word into individual flag characters."""
    if word.opaque:
        return frozenset()

    text = word.text
    if len(text) <= 1 or not text.startswith("-") or text.startswith("--"):
        return frozenset()
    return frozenset(text[1:])


def is_long_flag(word: ShellWord, name: str) -> bool:
    """Return whether ``word`` is exactly ``--<name>``."""
    return not word.opaque and word.text == f"--{name}"


def split_key_value_word(word: ShellWord) -> tuple[str, str] | None:
    """Split a non-opaque ``KEY=VALUE`` word."""
    if word.opaque:
        return None

    key, separator, value = word.text.partition("=")
    if not separator or not key:
        return None
    return key, value


def _view_pipeline(program: ShellProgram, node: Node) -> ShellPipeline:
    commands: list[ShellCommand] = []
    complete = True
    for child in node.named_children:
        command_node = _unwrap_redirected_command(child)
        if command_node is None:
            complete = False
            continue
        commands.append(_view_command(program, command_node))

    return ShellPipeline(
        commands=tuple(commands),
        complete=complete and bool(commands),
        node=node,
        has_error=_has_parse_uncertainty(node),
    )


def _view_command(program: ShellProgram, node: Node) -> ShellCommand:
    name: ShellWord | None = None
    words: list[ShellWord] = []
    assignments: list[ShellWord] = []
    found_name = False

    for child in node.named_children:
        if child.type == "command_name":
            name = _command_name_word(program, child)
            found_name = True
            continue

        if child.type == "variable_assignment" and not found_name:
            assignments.append(_shell_word(program, child))
            continue

        if not found_name or child.type in _COMMAND_CHILD_SKIP_TYPES:
            continue

        if "redirect" in child.type:
            continue

        words.append(_shell_word(program, child))

    return ShellCommand(
        name=name,
        words=tuple(words),
        assignments=tuple(assignments),
        node=node,
        has_error=_has_parse_uncertainty(node),
    )


def _command_name_word(program: ShellProgram, node: Node) -> ShellWord:
    text = node_text(program, node)
    named_children = node.named_children
    opaque = (
        len(named_children) != 1
        or named_children[0].type != "word"
        or _text_has_opaque_syntax(text)
    )
    return ShellWord(
        text=text,
        node_type=node.type,
        opaque=opaque,
        node=node,
    )


def _shell_word(program: ShellProgram, node: Node) -> ShellWord:
    text = node_text(program, node)
    return ShellWord(
        text=text,
        node_type=node.type,
        opaque=_is_opaque_word_node(node, text),
        node=node,
    )


def _is_opaque_word_node(node: Node, text: str) -> bool:
    if _text_has_opaque_syntax(text):
        return True

    if node.type == "word":
        return False

    if node.type == "variable_assignment":
        return any(
            child.type not in {"variable_name", "word"} for child in node.named_children
        )

    return True


def _text_has_opaque_syntax(text: str) -> bool:
    return not text or any(
        character.isspace() or character in _OPAQUE_WORD_CHARS for character in text
    )


def _has_parse_uncertainty(node: Node) -> bool:
    return node.has_error or _has_missing_descendant(node)


def _has_missing_descendant(node: Node) -> bool:
    if node.is_missing:
        return True
    return any(_has_missing_descendant(child) for child in node.children)


def _unwrap_redirected_command(node: Node) -> Node | None:
    current = node
    while current.type == "redirected_statement":
        command_children = [
            child for child in current.named_children if child.type == "command"
        ]
        if len(command_children) != 1:
            return None
        current = command_children[0]

    if current.type == "command":
        return current
    return None


def _iter_nodes(node: Node) -> Iterator[Node]:
    if node.is_named:
        yield node
    for child in node.children:
        yield from _iter_nodes(child)
