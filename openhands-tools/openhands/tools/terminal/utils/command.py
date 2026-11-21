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


# A command line cannot hold more heredocs than this in practice; the bound only
# keeps the scan below finite if the parser ever reports the same one twice.
_MAX_HEREDOCS: int = 64

_WORD_BREAK: frozenset[int] = frozenset(b" \t\n;&|<>()")


def _first_heredoc_word(source: bytes) -> int | None:
    """Byte offset of the first heredoc delimiter word tree-sitter reports."""
    first: int | None = None
    stack = [parse(source.decode(errors="replace")).tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "heredoc_start" and (first is None or node.start_byte < first):
            first = node.start_byte
        stack.extend(node.children)
    return first


def _read_heredoc_delimiter(
    source: bytes, start: int
) -> tuple[bytes, int, bool] | None:
    """Read the delimiter word at ``start`` and apply bash's quote removal.

    Returns the delimiter, the offset just past the word, and whether any part
    of the word was quoted, or ``None`` when the word cannot be read with
    certainty: a quote that is never closed, or an escape inside ``$'...'``.
    """
    delimiter = bytearray()
    quote: int | None = None
    ansi_c = False
    quoted = False
    index = start
    while index < len(source):
        char = source[index]
        if quote is None:
            if char in _WORD_BREAK:
                break
            if char == ord("$") and source[index + 1 : index + 2] in (b"'", b'"'):
                # $'...' and $"...": the dollar sign is part of the quoting.
                ansi_c = source[index + 1 : index + 2] == b"'"
                index += 1
                continue
            if char == ord("\\") and index + 1 < len(source):
                delimiter.append(source[index + 1])
                quoted = True
                index += 2
                continue
            if char in b"'\"":
                quote = char
                quoted = True
                index += 1
                continue
        elif char == quote:
            quote = None
            ansi_c = False
            index += 1
            continue
        elif ansi_c and char == ord("\\"):
            return None
        elif (
            quote == ord('"')
            and char == ord("\\")
            and source[index + 1 : index + 2] in (b'"', b"\\", b"$", b"`")
        ):
            delimiter.append(source[index + 1])
            index += 2
            continue
        delimiter.append(char)
        index += 1
    if quote is not None:
        return None
    return bytes(delimiter), index, quoted


def _end_of_heredoc_line(source: bytes, start: int) -> int | None:
    """Offset of the newline that ends the line holding a heredoc operator.

    Bash starts reading the body after the next newline *token*, so a newline
    that is escaped or sits inside quotes does not count, and a quote inside a
    trailing comment does not open a string. Returns ``len(source)`` when the
    input ends first, and ``None`` when the rest of the line cannot be read
    with certainty (an unclosed quote), so the caller can decline to judge.
    """
    index = start
    at_word_start = True
    while index < len(source):
        char = source[index]
        if char == ord("\n"):
            return index
        if char == ord("\\"):
            index += 2
            at_word_start = False
            continue
        if char == ord("#") and at_word_start:
            newline = source.find(b"\n", index)
            return len(source) if newline == -1 else newline
        if char in b"'\"":
            ansi_c = char == ord("'") and source[index - 1 : index] == b"$"
            index += 1
            while index < len(source) and source[index] != char:
                if source[index] == ord("\\") and (char == ord('"') or ansi_c):
                    index += 1
                index += 1
            if index >= len(source):
                return None
        at_word_start = char in _WORD_BREAK
        index += 1
    return len(source)


def find_unterminated_heredoc(commands: str) -> str | None:
    """Return the delimiter of a heredoc whose terminator never arrives.

    An interactive shell given such a command keeps reading the body: it shows
    no prompt, takes every later keystroke as body text, and so never reports
    that the command finished. Callers use this to refuse the command instead.

    tree-sitter-bash only locates each heredoc operator. Whether the body is
    closed is decided here with bash's own rule, because the grammar accepts
    terminators bash does not (an indented ``EOF``, ``EOF`` followed by a
    space, or any line that merely starts with the delimiter): the terminator
    is a line equal to the delimiter, after leading tabs are removed for
    ``<<-``, and with an unquoted delimiter a line ending in a backslash is
    joined to the next before the comparison. Each closed heredoc is cut out
    and the rest parsed again, so text inside a body is never mistaken for
    shell syntax.

    Returns ``None`` when every heredoc is closed, and also when the input
    cannot be read with certainty: a valid command must never be refused.
    """
    source = commands.encode()
    for _ in range(_MAX_HEREDOCS):
        word_start = _first_heredoc_word(source)
        if word_start is None:
            return None

        operator_end = word_start
        while operator_end > 0 and source[operator_end - 1] in b" \t":
            operator_end -= 1
        strip_tabs = source[operator_end - 1 : operator_end] == b"-"
        if strip_tabs:
            operator_end -= 1
        operator_start = operator_end - 2
        if (
            operator_start < 0
            or source[operator_start:operator_end] != b"<<"
            or source[operator_start - 1 : operator_start] == b"<"
        ):
            return None

        word = _read_heredoc_delimiter(source, word_start)
        if word is None:
            return None
        delimiter, word_end, quoted = word
        line_end = _end_of_heredoc_line(source, word_end)
        if line_end is None:
            return None

        body_start = min(line_end + 1, len(source))
        position = body_start
        body_end: int | None = None
        pending = b""
        while line_end < len(source):
            newline = source.find(b"\n", position)
            line = (
                pending + source[position : len(source) if newline == -1 else newline]
            )
            if (line.lstrip(b"\t") if strip_tabs else line) == delimiter:
                body_end = len(source) if newline == -1 else newline + 1
                break
            if newline == -1:
                break
            # With an unquoted delimiter bash removes a backslash-newline pair
            # and compares the joined line, so `text \` + `EOF` is not a
            # terminator while a lone `\` + `EOF` is.
            trailing = len(line) - len(line.rstrip(b"\\"))
            pending = line[:-1] if not quoted and trailing % 2 == 1 else b""
            position = newline + 1
        if body_end is None:
            return delimiter.decode(errors="replace")

        source = (
            source[:operator_start] + source[word_end:body_start] + source[body_end:]
        )
    return None


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
