"""Command splitting and escape utilities backed by tree-sitter-bash."""

import re

from tree_sitter import Node

from openhands.sdk.logger import get_logger
from openhands.sdk.security.shell_parser import parse


logger = get_logger(__name__)

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


def _ends_with_heredoc(node: Node) -> bool:
    """Whether ``node``'s final token is a heredoc terminator.

    Checking only that the last *named* descendant is a ``heredoc_end`` is not
    enough: tree-sitter leaves closing tokens such as ``fi``, ``done``, and
    ``)`` unnamed, so a compound statement or substitution that merely
    *contains* a heredoc would match and swallow the newline that actually ends
    the statement. Require the terminator to reach ``node``'s end too.
    """
    last = node
    while last.named_children:
        last = last.named_children[-1]
    return last.type == "heredoc_end" and last.end_byte == node.end_byte


def _last_heredoc_is_closed(root: Node) -> bool:
    """Whether the final heredoc in an unparseable input has seen its terminator.

    tree-sitter-bash reports a parse error for forms bash itself accepts (a
    quoted delimiter, several heredocs on one command, ``&`` before the body)
    and may then omit the ``heredoc_end`` node, leaving the terminator line
    inside the ``heredoc_body`` instead. Compare that final line against the
    delimiter to tell a closed heredoc from one bash is still reading. Deciding
    from the tree keeps the guard identical on every platform instead of
    depending on a ``bash`` executable being present and working.
    """
    entries: list[tuple[int, str, bytes]] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in ("heredoc_start", "heredoc_body", "heredoc_end"):
            entries.append((node.start_byte, node.type, node.text or b""))
        stack.extend(node.named_children)
    entries.sort()

    start = max(
        (
            index
            for index, (_, kind, _) in enumerate(entries)
            if kind == "heredoc_start"
        ),
        default=None,
    )
    if start is None:
        return False
    delimiter = re.sub(r"['\"]", "", entries[start][2].decode().strip())
    if delimiter.startswith("-"):
        delimiter = delimiter[1:]
    rest = entries[start + 1 :]
    if any(kind == "heredoc_end" for _, kind, _ in rest):
        return True
    bodies = [text for _, kind, text in rest if kind == "heredoc_body"]
    if not bodies:
        return False
    lines = [line for line in bodies[-1].decode().splitlines() if line.strip()]
    return bool(lines) and lines[-1].strip() == delimiter


def needs_heredoc_completion_boundary(commands: str) -> bool:
    """Whether interactive Bash may prompt before this input is fully consumed."""
    source = commands.encode()
    result = parse(commands)
    root = result.tree.root_node
    if result.has_error:
        stack = [root]
        has_heredoc = False
        while stack:
            node = stack.pop()
            has_heredoc = has_heredoc or node.type.startswith("heredoc_")
            stack.extend(node.named_children)
        if not has_heredoc:
            return False
        return _last_heredoc_is_closed(root)

    statements = [child for child in root.named_children if child.type != "comment"]
    return any(
        _ends_with_heredoc(current)
        and b"\n" in source[current.end_byte : following.start_byte]
        for current, following in zip(statements, statements[1:])
    )


def split_bash_commands(commands: str) -> list[str]:
    """Split a multi-statement bash input into top-level statements.

    Statements separated by a newline (with or without intermediate
    whitespace/comments) become separate entries, except after a completed
    heredoc. Statements joined by ``;``, ``&&``, ``||``, ``|``, or ``&`` stay
    together. Comments and whitespace between two statements are folded into
    the preceding entry. On parse failure the input is returned as a
    single-element list.
    """
    if not commands.strip():
        return [""]

    source = commands.encode()
    result = parse(commands)
    root = result.tree.root_node

    if result.has_error:
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
        if b"\n" in source[cur.end_byte : nxt.start_byte] and not _ends_with_heredoc(
            cur
        ):
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
    result = parse(command)
    if result.has_error:
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

    collect(result.tree.root_node)

    out = bytearray()
    cursor = 0
    for start, end in preserved:
        out.extend(_ESCAPE_PATTERN.sub(rb"\\\\\1", source[cursor:start]))
        out.extend(source[start:end])
        cursor = end
    out.extend(_ESCAPE_PATTERN.sub(rb"\\\\\1", source[cursor:]))

    return out.decode()
