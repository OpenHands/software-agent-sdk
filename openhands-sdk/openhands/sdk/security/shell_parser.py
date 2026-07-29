"""Shared tree-sitter-bash parsing entry point for the SDK.

Two consumers need a tree-sitter-bash parse of a shell command:

- ``openhands-tools`` for command splitting and escape doubling
  (``terminal.utils.command``).
- ``openhands-sdk`` for AST-aware security detection
  (the planned ``security.defense_in_depth.*`` analyzers).

Hosting the parser inside ``openhands-sdk`` lets both consumers depend
on the same tree-sitter version, avoids duplicate ``Language`` setup,
and keeps the security analyzers from acquiring a transitive
dependency on the tools package they need to scan.

This module intentionally exposes the minimum surface that both
consumers need today: a single ``parse(command)`` function returning a
``ParseResult`` that carries the parsed ``Tree`` and a precomputed
``has_error`` flag. Convenience iterators and a public ``root_node``
field are deferred to a follow-up so the first move is a no-behavior
shared substrate rather than an API surface enlargement.

The ``Parser`` is constructed per call. ``Language`` is built once at
import. This mirrors the convention in
``openhands-tools/openhands/tools/terminal/utils/command.py``: sharing
one parser across calls risks interleaved state, while the language
object is safely reusable.
"""

from dataclasses import dataclass

import tree_sitter_bash
from tree_sitter import Language, Parser, Tree


__all__ = ["ParseResult", "parse"]

_BASH_LANGUAGE = Language(tree_sitter_bash.language())


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Outcome of parsing a bash command with tree-sitter-bash.

    ``tree`` is the tree-sitter ``Tree`` produced from the input bytes;
    callers can walk it via the standard tree-sitter API. ``has_error``
    mirrors ``tree.root_node.has_error`` and is materialized at parse
    time so callers can branch without re-traversing the root.

    Frozen: callers must not mutate the result. Future revisions may
    add convenience accessors (e.g., ``iter_simple_commands``) without
    breaking existing consumers.
    """

    tree: Tree
    has_error: bool


def parse(command: str) -> ParseResult:
    """Parse a bash command string into a tree-sitter ``ParseResult``.

    Returns a ``ParseResult`` whose ``tree`` is the parsed tree-sitter
    ``Tree`` and whose ``has_error`` flag indicates whether the parser
    emitted any ``ERROR`` nodes during recovery. tree-sitter itself does
    not raise on syntactically malformed input; recovery is reported
    through ``ERROR`` nodes. Encoding the input string can raise:
    ``command.encode()`` uses strict UTF-8 and rejects lone surrogates
    with ``UnicodeEncodeError`` before the parser is invoked. Callers
    that must accept such inputs should normalize them upstream.

    Encoding is UTF-8; the parser operates on bytes internally, so
    callers reasoning about node spans should use byte offsets, not
    code-point offsets, against ``command.encode()``.
    """
    tree = Parser(_BASH_LANGUAGE).parse(command.encode())
    return ParseResult(tree=tree, has_error=tree.root_node.has_error)
