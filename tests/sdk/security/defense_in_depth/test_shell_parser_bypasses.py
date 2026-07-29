"""Bypass-class regression tests for the shell-parser direction of the
defense-in-depth security analyzer.

Why this file exists
--------------------
The security analyzers currently use regex matching against flattened shell
command text. Regex cannot understand quoting, escaping, or command-name
indirection -- the bypass classes encoded here exist by construction.

Issue #2721 tracks the migration to ``tree-sitter-bash``. Phase 1 (replacing
``bashlex`` in ``openhands-tools``) shipped as #3237. Phase 2 will move the
security analyzers onto the same parser substrate, at which point each
bypass below becomes structurally visible to the detector.

How to read it
--------------
Every test is marked ``xfail(strict=True)`` because it FAILS today against
the regex detectors and is EXPECTED TO PASS once Phase 2b lands.

When AST detection ships:

- xfails flip to passing automatically; no test edits required.
- ``strict=True`` fails the build on any unexpected pass, so a coverage
  regression in Phase 2b surfaces immediately.
- The bypass catalog becomes a no-touch acceptance test for the migration.

Scope discipline
----------------
Only bypass classes verified to evade the current regex detector at the
moment of authorship are encoded here. Several adjacent classes (wrapper
keywords like ``command rm``, block constructs like ``{ rm -rf /; }``,
path-qualified ``/bin/rm``) are already classified as HIGH by other
detector paths (eval/exec/word-boundary rules) and so are NOT bypasses
on current main; including them would overstate coverage. Bypasses that
remain undecidable even with AST (semantic base64 decoding, interpreter
list breadth, payloads past ``_EXTRACT_HARD_CAP``) are documented in
``test_adversarial.py`` and are deliberately not duplicated here.

Sources
-------
- @VascoSch92's bypass catalog in the issue body of #2721 (quoted segment).
- Adversarial source review against the PR #2718 working tree (command
  substitution, ANSI-C quoting).
"""

from __future__ import annotations

import json

import pytest

from openhands.sdk.event import ActionEvent
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.security.defense_in_depth.pattern import PatternSecurityAnalyzer
from openhands.sdk.security.risk import SecurityRisk


def make_action(command: str, tool_name: str = "bash") -> ActionEvent:
    """Create a minimal ActionEvent carrying ``command`` as the tool argument."""
    return ActionEvent(
        thought=[TextContent(text="test")],
        tool_name=tool_name,
        tool_call_id="test",
        tool_call=MessageToolCall(
            id="test",
            name=tool_name,
            arguments=json.dumps({"command": command}),
            origin="completion",
        ),
        llm_response_id="test",
    )


# ---------------------------------------------------------------------------
# Phase 2b bypass classes (strict xfails)
# ---------------------------------------------------------------------------


class TestCommandSubstitution:
    """Command name produced by substitution at runtime.

    The actor places the command name inside ``$(...)`` or backticks. The
    regex detector sees the literal substitution syntax in the first argv
    slot, not the post-expansion ``rm``.

    AST closure is policy-dependent. ``tree-sitter-bash`` exposes
    ``command_substitution`` as the sole child of ``command_name`` when
    the substitution appears in command position; the substituted
    command's text only exists as a ``word`` token nested inside the
    inner ``command`` node. Phase 2b must commit to one of:

    (a) recursing into the substitution body and treating inner command
        names as if they ran in command position, or
    (b) fail-closing on any ``command_substitution`` in command-name
        position.

    Either policy lands this test.
    """

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Regex sees literal substitution syntax, not the runtime name."
            " Closes with #2721 Phase 2b under fail-closed OR"
            " substitution-body-walk policy in command-name position."
        ),
    )
    @pytest.mark.parametrize(
        "command",
        [
            "$(echo rm) -rf /",
            "`echo rm` -rf /",
        ],
        ids=["dollar_paren", "backtick"],
    )
    def test_command_substitution_is_high(self, command: str):
        analyzer = PatternSecurityAnalyzer()
        risk = analyzer.security_risk(make_action(command))
        assert risk == SecurityRisk.HIGH


class TestQuotedSegment:
    """Quoting splits the command lexeme so ``\\brm\\s+`` boundary fails.

    Vasco's #2721 catalog names ``r"m" -rf /``: the closing quote ends
    the word at a non-whitespace character, so ``\\brm\\s+`` cannot
    anchor the destructive command. Empty single-quote concatenation
    (``r''m``) and fully quoted (``'rm'``) variants evade by the same
    mechanism.

    AST resolution: ``tree-sitter-bash`` recognises string concatenation
    and emits the post-expansion command name as a single string.
    """

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Quoted segments break \\brm\\s+ anchor in pattern.py."
            " Closes with #2721 Phase 2b."
        ),
    )
    @pytest.mark.parametrize(
        "command",
        [
            'r"m" -rf /',
            "r''m -rf /",
            "'rm' -rf /",
        ],
        ids=["double_quoted_concat", "empty_single_concat", "fully_quoted"],
    )
    def test_quoted_segment_is_high(self, command: str):
        analyzer = PatternSecurityAnalyzer()
        risk = analyzer.security_risk(make_action(command))
        assert risk == SecurityRisk.HIGH


class TestAnsiCQuoting:
    """ANSI-C quoting (``$'\\xNN'``) expands to literals only at shell runtime.

    The detector sees the literal ``$'...'`` token. The shell would
    expand it to ``rm`` before execution; the analyzer never gets that
    opportunity.

    AST closure is policy-dependent. ``tree-sitter-bash`` exposes
    ``ansi_c_string`` as a distinct node containing the raw, unexpanded
    escape sequence. Phase 2b must commit to one of:

    (a) decoding the escapes (``\\xHH``, ``\\NNN``, control chars) when
        the construct appears in command-name position, or
    (b) fail-closing on its presence.

    Either policy lands this test.
    """

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "ANSI-C quoting expands to 'rm' only at runtime."
            " Closes with #2721 Phase 2b under decode OR fail-closed policy"
            " on ansi_c_string in command-name position."
        ),
    )
    @pytest.mark.parametrize(
        "command",
        [
            r"$'\x72m' -rf /",
            r"$'\162\155' -rf /",
        ],
        ids=["hex_escape", "octal_escape"],
    )
    def test_ansi_c_quoting_is_high(self, command: str):
        analyzer = PatternSecurityAnalyzer()
        risk = analyzer.security_risk(make_action(command))
        assert risk == SecurityRisk.HIGH
