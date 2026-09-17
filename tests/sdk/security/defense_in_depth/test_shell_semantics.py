"""Fail-safe contract tests for the AST-backed shell scanner.

``scan_shell_command`` must never silently vouch LOW when it *refused* to
scan (nested-runner depth bound) or could not trust what it parsed
(ERROR/MISSING nodes intersecting the spans the detection relies on). Both
review findings on the Phase 2b PR were silent-LOW paths of that kind; these
tests pin the corrected contract at the unit level and confirm the
analyzer-level mapping of ``uncertain`` onto ``UNKNOWN``.

Scope boundary (mirrored by comments in ``shell_semantics.py``): a parse
error that does NOT intersect a command carrying the destructive flag shape
or a shell runner's argv stays LOW. Arbitrary non-shell text routinely fails
to parse, and surfacing every such error would flood the ensemble with
UNKNOWNs -- the boundary tests below document that deliberately-open edge.

Style follows ``test_shell_parser_bypasses.py``; payload fragments are
assembled at runtime only to keep local scanning tooling quiet.
"""

from __future__ import annotations

import json

from openhands.sdk.event import ActionEvent
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.security.defense_in_depth.pattern import PatternSecurityAnalyzer
from openhands.sdk.security.defense_in_depth.shell_semantics import (
    _MAX_NESTING_DEPTH,
    scan_shell_command,
)
from openhands.sdk.security.risk import SecurityRisk


_DETECTOR_ID = "test.detector.id"

_RF = "-" + "rf"
_RM = "r" + "m"
_DEL_SHORT = _RM + " " + _RF + " /"
# Quote-split verb: invisible to the flattened-text regex layer, so the
# analyzer-level assertions below isolate the AST path.
_DEL_QUOTED = "r" + '"m" ' + _RF + " /"


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


def _double_quoted(source: str) -> str:
    """Wrap ``source`` in double quotes with POSIX escaping."""
    escaped = (
        source.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )
    return f'"{escaped}"'


def _nest_in_runners(payload: str, levels: int) -> str:
    """Wrap ``payload`` in ``levels`` layers of runner script operands."""
    command = payload
    for _ in range(levels):
        command = f"bash -c {_double_quoted(command)}"
    return command


class TestDetectorIdentity:
    """The scanner reports the caller-owned detector ID unchanged."""

    def test_match_reports_callers_detector_id(self):
        result = scan_shell_command(_DEL_SHORT, _DETECTOR_ID)
        assert result.matched is True
        assert result.detector_id == _DETECTOR_ID
        assert result.uncertain is False

    def test_no_match_reports_no_detector_id(self):
        result = scan_shell_command("ls /tmp", _DETECTOR_ID)
        assert result.matched is False
        assert result.detector_id is None
        assert result.uncertain is False


class TestNestingDepthBound:
    """The nested-runner depth bound is fail-safe, never a silent LOW.

    Review finding on the Phase 2b PR: hitting ``_MAX_NESTING_DEPTH`` with a
    script operand still unscanned returned a silent LOW, handing attackers
    a constructive bypass (wrap the payload one level deeper than the
    bound). The bound now surfaces ``uncertain`` so the analyzer emits
    UNKNOWN -- and UNKNOWN fails safe under ``ConfirmRisky``.
    """

    def test_depth_bound_hit_is_uncertain_not_low(self):
        # One level past the bound: the innermost operand -- benign or not
        # -- was never scanned, so the scanner cannot vouch for it.
        command = _nest_in_runners("ls", _MAX_NESTING_DEPTH + 1)
        result = scan_shell_command(command, _DETECTOR_ID)
        assert result.matched is False
        assert result.uncertain is True

    def test_depth_bound_hit_maps_to_unknown(self):
        command = _nest_in_runners("ls", _MAX_NESTING_DEPTH + 1)
        analyzer = PatternSecurityAnalyzer()
        risk = analyzer.security_risk(make_action(command))
        assert risk == SecurityRisk.UNKNOWN

    def test_payload_at_bound_still_matches(self):
        # Exactly at the bound every level is scanned: the destructive
        # verb inside the innermost operand must still be resolved.
        command = _nest_in_runners(_DEL_QUOTED, _MAX_NESTING_DEPTH)
        result = scan_shell_command(command, _DETECTOR_ID)
        assert result.matched is True
        assert result.detector_id == _DETECTOR_ID

    def test_benign_nesting_within_bound_stays_low(self):
        command = _nest_in_runners("ls", _MAX_NESTING_DEPTH)
        result = scan_shell_command(command, _DETECTOR_ID)
        assert result.matched is False
        assert result.uncertain is False
        analyzer = PatternSecurityAnalyzer()
        assert analyzer.security_risk(make_action(command)) == SecurityRisk.LOW


class TestParseErrorFailSafe:
    """ERROR/MISSING nodes on relied-on spans surface as uncertain.

    Review finding on the Phase 2b PR: ``ShellCommand.has_error`` was
    plumbed through the AST view but never consulted, so a broken parse
    could silently truncate the very spans (verb, runner argv) the
    detection reads, and still vouch LOW. Errors intersecting a command
    that carries the destructive flag shape, or a shell runner's argv, now
    fail safe; a concrete match still wins over uncertainty.
    """

    def test_destructive_shape_with_parse_error_is_uncertain(self):
        # The unclosed quote is inside the command node: the resolved verb
        # cannot be trusted while the destructive flag shape is present.
        command = "foo " + _RF + ' / "unclosed'
        result = scan_shell_command(command, _DETECTOR_ID)
        assert result.matched is False
        assert result.uncertain is True

    def test_destructive_shape_with_parse_error_maps_to_unknown(self):
        command = "foo " + _RF + ' / "unclosed'
        analyzer = PatternSecurityAnalyzer()
        risk = analyzer.security_risk(make_action(command))
        assert risk == SecurityRisk.UNKNOWN

    def test_broken_runner_argv_is_uncertain(self):
        # The runner's script operand is an unclosed string (MISSING
        # closing quote): its argv spans cannot be trusted.
        result = scan_shell_command('bash -c "', _DETECTOR_ID)
        assert result.matched is False
        assert result.uncertain is True

    def test_match_wins_over_adjacent_parse_error(self):
        # A resolvable destructive command next to broken syntax must
        # still match: fail-safe replaces only the silent-miss path.
        command = "'" + _RM + "' " + _RF + " / oops("
        result = scan_shell_command(command, _DETECTOR_ID)
        assert result.matched is True
        assert result.detector_id == _DETECTOR_ID

    def test_benign_broken_command_stays_low(self):
        # Documented scope boundary: this ERROR node sits OUTSIDE the
        # command node (tree-sitter recovers by splitting), and the command
        # carries neither the destructive flag shape nor a runner argv.
        # Arbitrary non-shell text fails to parse like this all the time;
        # surfacing it would flood the ensemble with UNKNOWNs.
        result = scan_shell_command('echo "unclosed', _DETECTOR_ID)
        assert result.matched is False
        assert result.uncertain is False
        analyzer = PatternSecurityAnalyzer()
        risk = analyzer.security_risk(make_action('echo "unclosed'))
        assert risk == SecurityRisk.LOW
