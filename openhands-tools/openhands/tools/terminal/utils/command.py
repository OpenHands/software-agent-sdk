"""Tree-sitter-bash backed command splitting and escape utilities.

Replaces the previous bashlex implementation. bashlex is GPLv3+ and
unmaintained; tree-sitter and tree-sitter-bash are MIT-licensed and
recover gracefully on malformed input (no exception path needed).

Public API and fallback semantics are preserved: when the parse contains
``ERROR`` nodes, the input is returned unchanged — same behavior the
terminal session relied on with bashlex.
"""

from __future__ import annotations

import re

import tree_sitter_bash
from tree_sitter import Language, Node, Parser, Tree

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

_BASH_LANGUAGE = Language(tree_sitter_bash.language())

# Regions whose contents bash takes verbatim — escape doubling stops at
# their boundaries so operators nested inside (e.g.) a double-quoted
# string remain untouched. Walking does not recurse into these nodes.
_PRESERVE_TYPES: frozenset[str] = frozenset(
    {
        "string",
        "raw_string",
        "ansi_c_string",
        "translated_string",
        "command_substitution",
        "expansion",
        "simple_expansion",
        "heredoc_body",
        "comment",
    }
)

_ESCAPE_PATTERN: re.Pattern[bytes] = re.compile(rb"\\([;&|<>])")


def _parse(source: bytes) -> Tree:
    # Parser is not thread-safe; the Language is. Construct a fresh
    # Parser per call so concurrent callers cannot trample each other.
    return Parser(_BASH_LANGUAGE).parse(source)


def split_bash_commands(commands: str) -> list[str]:
    """Split a multi-statement bash input into top-level statements.

    Statements separated by a newline (with or without intermediate
    whitespace/comments) become separate entries; statements joined by
    ``;``, ``&&``, ``||``, ``|``, or ``&`` stay together. Comments and
    whitespace between two statements are folded into the preceding
    entry. On parse failure the input is returned as a single-element
    list.
    """
    if not commands.strip():
        return [""]

    source = commands.encode()
    tree = _parse(source)
    root = tree.root_node

    if root.has_error:
        logger.debug(
            "tree-sitter-bash reported parse errors; returning input as-is\n"
            "[input]: %s",
            commands,
        )
        return [commands]

    statements = [c for c in root.named_children if c.type != "comment"]
    if not statements:
        return [commands]

    boundaries = [statements[0].start_byte]
    for cur, nxt in zip(statements, statements[1:]):
        if b"\n" in source[cur.end_byte : nxt.start_byte]:
            boundaries.append(nxt.start_byte)
    boundaries.append(len(source))

    return [source[a:b].decode().rstrip() for a, b in zip(boundaries, boundaries[1:])]


def escape_bash_special_chars(command: str) -> str:
    r"""Double the escape on ``\;``, ``\&``, ``\|``, ``\<``, ``\>``.

    Sequences inside regions bash takes verbatim — single- and
    double-quoted strings, command substitutions, parameter expansions,
    heredoc bodies, and comments — are left untouched. On parse failure
    the input is returned unchanged.
    """
    if command.strip() == "":
        return ""

    source = command.encode()
    tree = _parse(source)
    if tree.root_node.has_error:
        logger.debug(
            "tree-sitter-bash reported parse errors; returning input as-is\n"
            "[input]: %s",
            command,
        )
        return command

    preserved: list[tuple[int, int]] = []

    def collect(node: Node) -> None:
        if node.type in _PRESERVE_TYPES:
            preserved.append((node.start_byte, node.end_byte))
            return
        for child in node.children:
            collect(child)

    collect(tree.root_node)

    out = bytearray()
    cursor = 0
    for start, end in preserved:
        out.extend(_ESCAPE_PATTERN.sub(rb"\\\\\1", source[cursor:start]))
        out.extend(source[start:end])
        cursor = end
    out.extend(_ESCAPE_PATTERN.sub(rb"\\\\\1", source[cursor:]))

    return out.decode()
