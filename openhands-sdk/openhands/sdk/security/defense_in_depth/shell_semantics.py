"""AST-backed shell-command detection for the pattern analyzer.

Resolves command names structurally against the shared tree-sitter
``_shell_ast`` view (issue #2721, Phase 2b) so that quoted, path-qualified,
and nested command names become visible to the recursive force-delete
detector. Only that family is scanned here; injection and Python-code
patterns stay as regex in ``pattern.py`` because they are not shell syntax.

The scanner reports ``uncertain`` -- so the analyzer can emit ``UNKNOWN``
rather than a false ``LOW``, the ensemble convention agreed in #2721
(UNKNOWN fails safe under ``ConfirmRisky``) -- whenever it cannot vouch for
what it saw:

- the destructive flag shape appears on a command whose verb cannot be
  resolved (an opaque or dynamic name);
- an ERROR/MISSING parse node intersects a span the detection relies on (a
  command carrying the destructive flag shape, or a shell runner's argv);
- the nested-runner depth bound is hit with a script operand still
  unscanned.

Benign text that merely fails to parse as shell is not treated as
uncertain: parse errors outside the spans above stay LOW, because arbitrary
non-shell text routinely fails to parse and surfacing every such error
would flood the ensemble with UNKNOWNs. Detector IDs are owned by
``pattern.py`` and passed in unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from tree_sitter import Node

from openhands.sdk.security import _shell_ast as ast_view
from openhands.sdk.security._shell_ast import (
    ShellCommand,
    ShellProgram,
    ShellWord,
)


# Command basenames whose script operand is a nested shell program.
# Resolving and re-parsing that operand closes the nested-runner bypass
# class without hardcoding payloads.
_SHELL_RUNNERS: Final[frozenset[str]] = frozenset(
    {"sh", "bash", "dash", "zsh", "ksh", "ash"}
)

# Basename of the recursive force-delete command resolved structurally.
_RM_BASENAME: Final[str] = "rm"

# The short-option character that makes a runner's first operand a script.
_SCRIPT_FLAG_CHAR: Final[str] = "c"

# End-of-options marker that terminates option parsing (POSIX).
_END_OF_OPTIONS: Final[str] = "--"

# A lone dash: the historical end-of-options synonym POSIX sh still accepts.
_BARE_DASH: Final[str] = "-"

# Leading characters that mark a runner argv word as an option group. POSIX
# sh accepts both ``-x``-style and ``+x``-style groups.
_OPTION_PREFIXES: Final[frozenset[str]] = frozenset({"-", "+"})

# Runner short options that consume the next word as their argument (set -o /
# shopt -O names). Without this, ``bash -o errexit -c ...`` would misread the
# option argument as the first operand and stop looking for the script flag.
_SHORT_OPTS_WITH_ARG: Final[frozenset[str]] = frozenset({"o", "O"})

# Runner long options that consume the next word as their argument. Exotic
# long options beyond these are out of scope: an unrecognized long option is
# treated as argumentless, which at worst misreads its argument as the first
# operand and stops descending -- it never over-descends.
_LONG_OPTS_WITH_ARG: Final[frozenset[str]] = frozenset({"--rcfile", "--init-file"})

# Long-flag names mapped onto their short-flag characters below.
_LONG_RECURSIVE: Final[str] = "recursive"
_LONG_FORCE: Final[str] = "force"

# Node types that make a command name dynamic (value known only at runtime).
# Any such child makes the name unresolvable at analysis time, so resolution
# fails and the caller falls back.
_DYNAMIC_NAME_NODE_TYPES: Final[frozenset[str]] = frozenset(
    {
        "command_substitution",
        "process_substitution",
        "expansion",
        "simple_expansion",
        "arithmetic_expansion",
        "ansi_c_string",
    }
)

# How deep nested script operands are followed. Bounds work on adversarial
# deeply-nested runner arguments; hitting the bound with an operand still
# unscanned is surfaced as uncertain, never a silent LOW.
_MAX_NESTING_DEPTH: Final[int] = 8

# Characters a backslash escapes inside double quotes (POSIX 2.2.3). Before
# any other character the backslash is literal and must be retained.
_DOUBLE_QUOTE_ESCAPABLE: Final[frozenset[str]] = frozenset('$`"\\')


@dataclass(frozen=True, slots=True)
class ShellScanResult:
    """Outcome of an AST-backed shell scan.

    ``detector_id`` is the matched pattern stable ID when ``matched`` is
    True, else None. ``uncertain`` is True when the scan saw something it
    could not vouch for (destructive flag shape on an unresolvable verb, a
    parse error on a relied-on span, or the nesting depth bound hit),
    signalling the caller to emit ``UNKNOWN`` instead of ``LOW``.
    """

    matched: bool
    detector_id: str | None = None
    uncertain: bool = False


@dataclass(frozen=True, slots=True)
class SignalOutput:
    """Outcome of scanning one program or command for a destructive verb.

    ``matched`` is True once a recursive force delete is resolved concretely.
    ``uncertain`` is True when the scan saw a destructive shape it could not
    vouch for, which the caller turns into ``UNKNOWN`` rather than ``LOW``.

    A concrete match always outranks uncertainty, so ``matched`` and
    ``uncertain`` are never both True.
    """

    matched: bool
    uncertain: bool


_NO_SIGNAL: Final[SignalOutput] = SignalOutput(matched=False, uncertain=False)
_MATCH_SIGNAL: Final[SignalOutput] = SignalOutput(matched=True, uncertain=False)
_UNCERTAIN_SIGNAL: Final[SignalOutput] = SignalOutput(matched=False, uncertain=True)


def scan_shell_command(command: str, rm_detector_id: str) -> ShellScanResult:
    """Scan ``command`` for AST-resolvable destructive shell commands.

    Resolves each simple command name through quotes and path prefixes,
    descends into shell-runner script operands, and reports a match for the
    recursive force-delete family.

    Uncertainty is reported narrowly, only when the scan cannot vouch for
    what it saw: a command carries the recursive-and-force flag shape but
    its verb cannot be trusted (opaque or dynamic name, or an ERROR/MISSING
    parse node intersecting the command), a shell runner's argv is touched
    by a parse error, or the nesting depth bound was hit with a script
    operand still unscanned. In each case the caller emits UNKNOWN rather
    than a false LOW. A benign string that merely fails to parse as shell
    is not treated as uncertain; it stays LOW.
    """
    program = _safe_parse(command)
    if program is None:
        # Encoding failure (e.g. lone surrogate). The regex layer already
        # scanned the flattened text; report neither match nor uncertainty.
        return ShellScanResult(matched=False)

    signal = _program_signal(program, depth=0)
    if signal.matched:
        return ShellScanResult(matched=True, detector_id=rm_detector_id)
    return ShellScanResult(matched=False, uncertain=signal.uncertain)


def _safe_parse(command: str) -> ShellProgram | None:
    try:
        return ast_view.parse_shell_program(command)
    except (UnicodeEncodeError, ValueError):
        return None


def _program_signal(program: ShellProgram, depth: int) -> SignalOutput:
    """Return the scan signal for a parsed program.

    ``matched`` is True when a recursive force delete is resolved. ``uncertain``
    is True when the destructive flag shape is present on a command whose verb
    cannot be resolved statically.
    """
    uncertain = False
    for command in ast_view.iter_commands(program):
        resolved = _resolve_command_basename(command)
        if _has_recursive_force_flags(command):
            if resolved == _RM_BASENAME:
                return _MATCH_SIGNAL
            if resolved is None or command.has_error:
                # Dangerous flag shape on a verb we cannot vouch for:
                # either the name is unresolvable, or an ERROR/MISSING
                # parse node intersects this command, so the resolved verb
                # may not be the verb the shell would run. Keep scanning
                # for a concrete match first.
                uncertain = True
        nested = _nested_signal(command, resolved, depth)
        if nested.matched:
            return _MATCH_SIGNAL
        uncertain = uncertain or nested.uncertain
    return SignalOutput(matched=False, uncertain=uncertain)


def _has_recursive_force_flags(command: ShellCommand) -> bool:
    """Return whether ``command`` carries recursive and force flags together."""
    flags = _collect_flags(command.words)
    has_recursive = "r" in flags or "R" in flags
    has_force = "f" in flags
    return has_recursive and has_force


def _collect_flags(words: tuple[ShellWord, ...]) -> set[str]:
    """Collect short-flag chars and recognised long flags.

    The end-of-options marker terminates flag parsing. Long flags are mapped
    onto their short-flag character so ordering and mixing work.
    """
    flags: set[str] = set()
    for word in words:
        if not word.opaque and word.text == _END_OF_OPTIONS:
            break
        flags |= ast_view.split_short_flags(word)
        if ast_view.is_long_flag(word, _LONG_RECURSIVE):
            flags.add("r")
        elif ast_view.is_long_flag(word, _LONG_FORCE):
            flags.add("f")
    return flags


def _nested_signal(
    command: ShellCommand,
    resolved_name: str | None,
    depth: int,
) -> SignalOutput:
    """Descend into a shell runner script operand and re-parse it.

    Closes the nested-runner bypass class, including quoted verbs inside the
    script that no outer pattern can see.

    Fail-safe ordering: the operand scan runs first so a concrete match
    always wins; only when no match is found does a parse error on the
    runner's own node force uncertainty -- an ERROR/MISSING there (e.g. an
    unclosed quote truncating the script operand) means the argv spans the
    scan just read cannot be trusted, so a silent LOW would vouch for
    something never actually seen.
    """
    if resolved_name not in _SHELL_RUNNERS:
        return _NO_SIGNAL

    operand = _scan_script_operand(command, depth)
    if operand.matched:
        return _MATCH_SIGNAL
    if command.has_error:
        return _UNCERTAIN_SIGNAL
    return operand


def _scan_script_operand(command: ShellCommand, depth: int) -> SignalOutput:
    """Extract, bound-check, and recursively scan a runner script operand.

    A dynamic (unresolvable) script operand is deliberately NOT treated as
    uncertain: passing a shell variable as the script is ordinary benign
    usage, and flagging it would flood the caller with UNKNOWNs. Uncertainty
    is reserved for the direct destructive-flags-on-unvouchable-verb shape
    in ``_program_signal`` and the depth bound below.
    """
    has_flag, inner = _extract_script_operand(command)
    if not has_flag or inner is None:
        return _NO_SIGNAL

    if depth >= _MAX_NESTING_DEPTH:
        # Depth bound hit with a real script operand still unscanned: we
        # refused to look, so we cannot vouch. Surface uncertainty (the
        # analyzer emits UNKNOWN) rather than a silent LOW that would hand
        # attackers a constructive bypass one nesting level past the bound.
        return _UNCERTAIN_SIGNAL

    inner_program = _safe_parse(inner)
    if inner_program is None:
        return _NO_SIGNAL
    return _program_signal(inner_program, depth + 1)


def _extract_script_operand(
    command: ShellCommand,
) -> tuple[bool, str | None]:
    """Locate the runner script operand using POSIX argv option semantics.

    Short options combine, so the script flag counts at any position in a
    group (``-xc``, ``-cx``); an argument-taking option consumes the next
    word; ``--`` (or the historical lone ``-``) terminates option parsing;
    and the first operand ends the option list. With the script flag set,
    that first operand IS the script. Without it, the first operand is a
    script *file*, and any flag word after it -- or after ``--`` -- belongs
    to that script rather than to the runner, so it must not trigger a
    descent.

    Returns ``(has_flag, operand)``. ``has_flag`` is True when the script
    flag is present in the option list. ``operand`` is the de-quoted,
    escape-normalized literal text of the script operand, or None when the
    flag has no operand or the operand is dynamic (unresolvable at analysis
    time).
    """
    has_script_flag = False
    options_ended = False
    words = command.words
    index = 0
    while index < len(words):
        literal = _resolve_node_literal(words[index].node)
        if literal is None:
            # A dynamic argv word cannot be classified as option or operand
            # at analysis time. Treat it as the first operand: a dynamic
            # script operand is deliberately not uncertain (see
            # _scan_script_operand), and assuming an option here could
            # over-descend into text the runner would never execute.
            return has_script_flag, None
        if not options_ended:
            if literal == _END_OF_OPTIONS or literal == _BARE_DASH:
                options_ended = True
                index += 1
                continue
            if _is_option_word(literal):
                if _sets_script_flag(literal):
                    has_script_flag = True
                index += 2 if _consumes_next_word(literal) else 1
                continue
        # First operand reached.
        if has_script_flag:
            return True, literal
        return False, None
    return has_script_flag, None


def _is_option_word(literal: str) -> bool:
    """Return whether a resolved argv word is an option group."""
    return len(literal) > 1 and literal[0] in _OPTION_PREFIXES


def _sets_script_flag(literal: str) -> bool:
    """Return whether a short-option group contains the script flag.

    Only ``-`` groups can set it: ``--long`` words are not short groups,
    and ``+`` groups unset options rather than set them.
    """
    return (
        literal.startswith(_BARE_DASH)
        and not literal.startswith(_END_OF_OPTIONS)
        and _SCRIPT_FLAG_CHAR in literal[1:]
    )


def _consumes_next_word(literal: str) -> bool:
    """Return whether an option word takes the next word as its argument.

    For short groups the argument-taking option must be the last character
    (``-o errexit``); anywhere else the group is malformed and the runner
    would reject it, so nothing is consumed.
    """
    if literal in _LONG_OPTS_WITH_ARG:
        return True
    if literal.startswith(_END_OF_OPTIONS):
        return False
    return literal[-1] in _SHORT_OPTS_WITH_ARG


def _resolve_command_basename(command: ShellCommand) -> str | None:
    """Resolve a command name to its POSIX basename, seeing through quotes.

    A bare verb resolves to itself; a path-qualified verb resolves to its
    final segment; a quoted verb resolves to the de-quoted text. Returns
    None when the name is dynamic (command substitution, expansion, ANSI-C
    string) or absent.
    """
    if command.name is None:
        return None
    literal = _resolve_node_literal(command.name.node)
    if literal is None:
        return None
    return _posix_basename(literal)


def _resolve_node_literal(node: Node) -> str | None:
    """Concatenate literal text from a name/word node, or None if dynamic.

    Walks the concatenation/string/raw_string/word structure that
    tree-sitter-bash produces for command names and arguments. Any dynamic
    child (substitution, expansion, ANSI-C string) makes the value
    unresolvable at analysis time and yields None.
    """
    parts = _collect_literal_parts(node)
    if parts is None:
        return None
    return "".join(parts)


def _collect_literal_parts(node: Node) -> list[str] | None:
    """Return the literal fragments of ``node``, or None if it is dynamic.

    None means the subtree is not statically resolvable. Fragments are
    escape-normalized to the effective literal the shell would produce after
    quote removal: tree-sitter keeps backslash escapes inline in ``word`` and
    ``string_content`` text, so comparing raw text would let ``r\\m`` (which
    the shell runs as ``rm``) slip past the basename check.
    """
    if node.type in _DYNAMIC_NAME_NODE_TYPES:
        return None

    match node.type:
        case "word":
            return [_unescape_unquoted(_decode(node.text))]

        case "raw_string":
            # Single-quoted operand: strip the quotes; contents are literal
            # and backslash has no special meaning inside single quotes.
            text = _decode(node.text)
            return [text[1:-1] if len(text) >= 2 else ""]

        case "string_content":
            return [_unescape_double_quoted(_decode(node.text))]

        # "string" is the double-quoted case: recursing catches an embedded
        # expansion as dynamic while string_content fragments contribute
        # literally. command_name and concatenation recurse for the same
        # reason, so all three share one branch.
        case "string" | "command_name" | "concatenation":
            parts: list[str] = []
            for child in node.named_children:
                child_parts = _collect_literal_parts(child)
                if child_parts is None:
                    return None
                parts.extend(child_parts)
            return parts

        # Unknown / unhandled node type in a name position: treat as dynamic
        # so we never fabricate a resolved name we are not sure about.
        case _:
            return None


def _unescape_unquoted(text: str) -> str:
    """Strip backslash escapes from an unquoted word (POSIX 2.2.1).

    Outside quotes a backslash makes the next character literal, so the
    effective value drops the backslash (``r\\m`` -> ``rm``); a
    backslash-newline pair is a line continuation and disappears entirely.
    """
    return _strip_backslash_escapes(text, escapable=None)


def _unescape_double_quoted(text: str) -> str:
    """Strip the escapes double quotes honour (POSIX 2.2.3).

    Inside double quotes a backslash only escapes ``$``, `````, ``"``,
    ``\\`` and newline; before any other character the backslash itself is
    literal and must be retained (``"r\\m"`` stays ``r\\m``).
    """
    return _strip_backslash_escapes(text, escapable=_DOUBLE_QUOTE_ESCAPABLE)


def _strip_backslash_escapes(text: str, escapable: frozenset[str] | None) -> str:
    """Return ``text`` with backslash escapes reduced to their literals.

    ``escapable`` is the set of characters whose escapes are honoured, or
    None to honour every character. Backslash-newline is always a line
    continuation and yields nothing. A trailing lone backslash is kept.
    """
    parts: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\\" and index + 1 < length:
            follower = text[index + 1]
            if follower == "\n":
                index += 2
                continue
            if escapable is None or follower in escapable:
                parts.append(follower)
                index += 2
                continue
        parts.append(char)
        index += 1
    return "".join(parts)


def _posix_basename(path: str) -> str:
    """Return the final path segment (basename) without importing posixpath.

    A trailing slash yields an empty basename, matching POSIX ``basename``
    semantics closely enough for command-name resolution.
    """
    return path.rsplit("/", 1)[-1]


def _decode(raw: bytes | None) -> str:
    return raw.decode() if raw is not None else ""
