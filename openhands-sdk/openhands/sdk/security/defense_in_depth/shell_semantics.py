"""AST-backed shell-command detection for the pattern analyzer.

Resolves command names structurally against the shared tree-sitter
``_shell_ast`` view (issue #2721, Phase 2b) so that quoted, path-qualified,
and nested command names become visible to the recursive force-delete
detector. Only that family is scanned here; injection and Python-code
patterns stay as regex in ``pattern.py`` because they are not shell syntax.

When the destructive flag shape appears on a command whose verb cannot be
resolved (an opaque or dynamic name, which the parser often also marks with
an ERROR node), the scanner reports ``uncertain`` so the analyzer can emit
``UNKNOWN`` rather than a false ``LOW`` -- the ensemble convention agreed in
#2721 (UNKNOWN fails safe under ``ConfirmRisky``). Benign text that merely
fails to parse as shell is not treated as uncertain. Detector IDs are owned
by ``pattern.py`` and passed in unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

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
_SHELL_RUNNERS = frozenset({"sh", "bash", "dash", "zsh", "ksh", "ash"})

# Basename of the recursive force-delete command resolved structurally.
_RM_BASENAME = "rm"

# The runner script-operand flag.
_SCRIPT_FLAG = "-" + "c"

# End-of-options marker that terminates flag parsing (POSIX).
_END_OF_OPTIONS = "--"

# Long-flag names mapped onto their short-flag characters below.
_LONG_RECURSIVE = "recursive"
_LONG_FORCE = "force"

# Node types that make a command name dynamic (value known only at runtime).
# Any such child makes the name unresolvable at analysis time, so resolution
# fails and the caller falls back.
_DYNAMIC_NAME_NODE_TYPES = frozenset(
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
# deeply-nested runner arguments.
_MAX_NESTING_DEPTH = 8


@dataclass(frozen=True, slots=True)
class ShellScanResult:
    """Outcome of an AST-backed shell scan.

    ``detector_id`` is the matched pattern stable ID when ``matched`` is
    True, else None. ``uncertain`` is True when the destructive flag shape
    was found on a command whose verb could not be resolved, signalling the
    caller to emit ``UNKNOWN`` instead of ``LOW``.
    """

    matched: bool
    detector_id: str | None = None
    uncertain: bool = False


def scan_shell_command(command: str, rm_detector_id: str) -> ShellScanResult:
    """Scan ``command`` for AST-resolvable destructive shell commands.

    Resolves each simple command name through quotes and path prefixes,
    descends into shell-runner script operands, and reports a match for the
    recursive force-delete family.

    Uncertainty is reported narrowly: only when a command carries the
    recursive-and-force flag shape but its verb is unresolvable (opaque or
    dynamic name) -- the analyzer can see the dangerous shape but cannot
    confirm the verb, so the caller emits UNKNOWN rather than a false LOW.
    A benign string that merely fails to parse as shell is not treated as
    uncertain; it stays LOW.
    """
    program = _safe_parse(command)
    if program is None:
        # Encoding failure (e.g. lone surrogate). The regex layer already
        # scanned the flattened text; report neither match nor uncertainty.
        return ShellScanResult(matched=False)

    matched, uncertain = _program_signal(program, depth=0)
    if matched:
        return ShellScanResult(matched=True, detector_id=rm_detector_id)
    return ShellScanResult(matched=False, uncertain=uncertain)


def _safe_parse(command: str) -> ShellProgram | None:
    try:
        return ast_view.parse_shell_program(command)
    except (UnicodeEncodeError, ValueError):
        return None


def _program_signal(program: ShellProgram, depth: int) -> tuple[bool, bool]:
    """Return ``(matched, uncertain)`` for a parsed program.

    ``matched`` is True when a recursive force delete is resolved. ``uncertain``
    is True when the destructive flag shape is present on a command whose verb
    cannot be resolved statically.
    """
    uncertain = False
    for command in ast_view.iter_commands(program):
        resolved = _resolve_command_basename(command)
        if _has_recursive_force_flags(command):
            if resolved == _RM_BASENAME:
                return True, False
            if resolved is None:
                # Dangerous flag shape on an unresolvable verb: we cannot
                # vouch for it. Keep scanning for a concrete match first.
                uncertain = True
        nested_matched, nested_uncertain = _nested_signal(command, resolved, depth)
        if nested_matched:
            return True, False
        uncertain = uncertain or nested_uncertain
    return False, uncertain


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
) -> tuple[bool, bool]:
    """Descend into a shell runner script operand and re-parse it.

    Closes the nested-runner bypass class, including quoted verbs inside the
    script that no outer pattern can see. Returns ``(matched, uncertain)``.

    A dynamic (unresolvable) script operand is deliberately NOT treated as
    uncertain: passing a shell variable as the script is ordinary benign
    usage, and flagging it would flood the caller with UNKNOWNs. Uncertainty
    is reserved for the direct destructive-flags-on-unresolvable-verb shape
    in ``_program_signal``.
    """
    if depth >= _MAX_NESTING_DEPTH:
        return False, False
    if resolved_name not in _SHELL_RUNNERS:
        return False, False

    has_flag, inner = _extract_script_operand(command)
    if not has_flag or inner is None:
        return False, False

    inner_program = _safe_parse(inner)
    if inner_program is None:
        return False, False
    return _program_signal(inner_program, depth + 1)


def _extract_script_operand(
    command: ShellCommand,
) -> tuple[bool, str | None]:
    """Locate the script flag operand and resolve it to literal text.

    Returns ``(has_flag, operand)``. ``has_flag`` is True when the script
    flag is present. ``operand`` is the de-quoted literal text of the word
    after the flag, or None when the flag has no operand or the operand is
    dynamic (unresolvable at analysis time).
    """
    words = command.words
    for index, word in enumerate(words):
        if word.opaque or word.text != _SCRIPT_FLAG:
            continue
        if index + 1 >= len(words):
            return True, None
        return True, _resolve_word_literal(words[index + 1])
    return False, None


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


def _resolve_word_literal(word: ShellWord) -> str | None:
    """Resolve an argument word to its literal text, seeing through quotes."""
    return _resolve_node_literal(word.node)


def _resolve_node_literal(node: Node) -> str | None:
    """Concatenate literal text from a name/word node, or None if dynamic.

    Walks the concatenation/string/raw_string/word structure that
    tree-sitter-bash produces for command names and arguments. Any dynamic
    child (substitution, expansion, ANSI-C string) makes the value
    unresolvable at analysis time and yields None.
    """
    parts: list[str] = []
    if not _collect_literal_parts(node, parts):
        return None
    return "".join(parts)


def _collect_literal_parts(node: Node, parts: list[str]) -> bool:
    """Append literal fragments from ``node``; return False if dynamic.

    Returns True when the whole subtree is statically resolvable.
    """
    node_type = node.type

    if node_type in _DYNAMIC_NAME_NODE_TYPES:
        return False

    if node_type == "word":
        parts.append(_decode(node.text))
        return True

    if node_type == "raw_string":
        # Single-quoted operand: strip the quotes; contents are literal.
        text = _decode(node.text)
        parts.append(text[1:-1] if len(text) >= 2 else "")
        return True

    if node_type == "string_content":
        parts.append(_decode(node.text))
        return True

    if node_type == "string":
        # Double-quoted: recurse so an embedded expansion is caught as
        # dynamic, while string_content fragments contribute literally.
        for child in node.named_children:
            if not _collect_literal_parts(child, parts):
                return False
        return True

    if node_type in {"command_name", "concatenation"}:
        for child in node.named_children:
            if not _collect_literal_parts(child, parts):
                return False
        return True

    # Unknown / unhandled node type in a name position: treat as dynamic so
    # we never fabricate a resolved name we are not sure about.
    return False


def _posix_basename(path: str) -> str:
    """Return the final path segment (basename) without importing posixpath.

    A trailing slash yields an empty basename, matching POSIX ``basename``
    semantics closely enough for command-name resolution.
    """
    return path.rsplit("/", 1)[-1]


def _decode(raw: bytes | None) -> str:
    return raw.decode() if raw is not None else ""
