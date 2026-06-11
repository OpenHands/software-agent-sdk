"""Tests for SupplyChainSecurityAnalyzer at the action boundary.

Mirrors the make_action helper pattern from
tests/sdk/security/defense_in_depth/test_pattern.py: an ActionEvent whose
tool_call.arguments is json.dumps({"command": cmd}). Confirms the analyzer
returns HIGH on a typosquat install and LOW otherwise, scans only the
executable corpus (not reasoning text), and never hard-denies.
"""

from __future__ import annotations

import json

import pytest

from openhands.sdk.event import ActionEvent
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.security.confirmation_policy import ConfirmRisky
from openhands.sdk.security.risk import SecurityRisk
from openhands.sdk.security.supply_chain import SupplyChainSecurityAnalyzer


# ---------------------------------------------------------------------------
# Test helper (mirrors test_pattern.py)
# ---------------------------------------------------------------------------


def make_action(
    command: str, tool_name: str = "bash", **extra_fields: str
) -> ActionEvent:
    """Create a minimal ActionEvent for testing."""
    kwargs: dict = dict(
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
    kwargs.update(extra_fields)
    return ActionEvent(**kwargs)


# ---------------------------------------------------------------------------
# HIGH: typosquat installs
# ---------------------------------------------------------------------------

_HIGH_CASES = [
    ("npm install lodahs", "lodash typosquat"),
    ("npm i -D lodahs", "short flag before package"),
    ('npm install "lodahs"', "double-quoted package"),
    ("npm -g install lodahs", "global flag before subcommand"),
    ("npm --prefix ui install lodahs", "value-flag before subcommand"),
    ("FOO=bar npm install lodahs", "env assignment prefix"),
    ("sudo npm install lodahs", "sudo wrapper"),
    ("env FOO=bar npm install lodahs", "env wrapper inline assignment"),
    ("npm install expres", "express typosquat"),
    ("npm install typscript", "typescript missing letter"),
    ("cd x && npm install lodahs", "after &&"),
    ("cd ui\nnpm install lodahs", "after newline"),
    ("echo hi | npm install lodahs", "after pipe"),
    ("npx expres", "runner target"),
    ("npx --package=lodahs some-bin", "runner --package="),
    ("npx -p lodahs some-bin", "runner -p name"),
    ("bunx expres", "bunx runner"),
    ("yarn add lodahs", "yarn add"),
    ("pnpm add lodahs", "pnpm add"),
    ("bun add lodahs", "bun add"),
    ("pnpm dlx expres", "pnpm dlx"),
    ("npm install lodahs@4.17.21", "version-stripped"),
    ("/usr/bin/npm install lodahs", "path-prefixed binary"),
    ("npm install lodahs expres", "multiple typosquats"),
    ("npm install --unknownflag axio lodash", "unknown flag swallows no value"),
    ("npm install --registry http://x lodahs", "known value flag skips its value"),
    ("npm install --before lodahs axio", "value flag eats lodahs, axio remains"),
    ("npm install --omit=dev lodahs", "equals-form value flag self-contained"),
    ("npx --registry http://x expres", "runner skips value flag before target"),
    ("npm --tag beta install loadsh", "tag value flag before subcommand"),
    ("npm --audit-level high install loadsh", "audit-level value flag before sub"),
    ("npm --install-strategy nested install loadsh", "install-strategy value flag"),
    ("npm --scope @myscope install loadsh", "scope value flag before subcommand"),
    ("npm --otp 123456 install loadsh", "otp value flag before subcommand"),
    ("npm --maxsockets 5 install loadsh", "maxsockets value flag before subcommand"),
    ("npm --tag install install loadsh", "tag value equals install keyword"),
    ("npm --foo bar install lodahs", "robust scan skips non-subcommand to install"),
    ("npm --someunknownvalflag X install loadsh", "unknown value flag backstop"),
    ("yarn --cwd /tmp add loadsh", "yarn cwd value flag before add"),
    ("pnpm --filter foo add loadsh", "pnpm filter value flag before add"),
    ("bun --cwd /tmp add loadsh", "bun cwd value flag before add"),
    ("npm install --legacy-peer-deps loadsh", "legacy-peer-deps boolean not value"),
]


@pytest.mark.parametrize("command,desc", _HIGH_CASES, ids=[c[1] for c in _HIGH_CASES])
def test_analyzer_high(command: str, desc: str):
    analyzer = SupplyChainSecurityAnalyzer()
    risk = analyzer.security_risk(make_action(command))
    assert risk == SecurityRisk.HIGH, f"{desc}: expected HIGH, got {risk}"
    # HIGH risk requires confirmation -- the human is asked, not auto-denied.
    assert ConfirmRisky().should_confirm(risk) is True


# ---------------------------------------------------------------------------
# LOW: clean installs and unrelated commands
# ---------------------------------------------------------------------------

_LOW_CASES = [
    ("npm install lodash", "legitimate lodash"),
    ("npm install react react-dom", "multiple legit packages"),
    ("npm install --prefix axio lodash", "axio is a flag value"),
    ("npm install vu", "short popular name (<=4 chars)"),
    ("npm install lodaaash", "two edits away"),
    ("npm run build", "npm run build"),
    ("npm install", "bare install no packages"),
    ("npm run add lodahs", "script named add"),
    ("yarn add", "yarn add no packages"),
    ("npx create-react-app my-app", "runner trailing arg not target"),
    ("npm install @scope/pkg@1.2.3", "scoped package"),
    ("git status", "unrelated command"),
    ("ls /tmp", "ls"),
    ("pip install requestss", "non-npm manager out of scope"),
    ("npx --some-flag value lodahs", "runner first positional is the target"),
    ("npm --tag loadsh install lodash", "tag eats typosquat value, lodash legit"),
    ("npm run build install lodahs", "run is a real subcommand, scan stops"),
    ("npm --foo bar baz lodahs", "no real subcommand after skips stays clean"),
    ("", "empty command"),
]


@pytest.mark.parametrize("command,desc", _LOW_CASES, ids=[c[1] for c in _LOW_CASES])
def test_analyzer_low(command: str, desc: str):
    analyzer = SupplyChainSecurityAnalyzer()
    risk = analyzer.security_risk(make_action(command))
    assert risk == SecurityRisk.LOW, f"{desc}: expected LOW, got {risk}"
    assert ConfirmRisky().should_confirm(risk) is False


# ---------------------------------------------------------------------------
# Two-corpus invariant: reasoning text must not trip the analyzer
# ---------------------------------------------------------------------------


def test_reasoning_text_does_not_trip_analyzer():
    """A typosquat mentioned only in reasoning/summary must stay LOW.

    The executable command is a harmless `ls /tmp`; the dangerous-looking
    `npm install lodahs` appears only in thought/reasoning/summary, which the
    analyzer must not scan.
    """
    action = ActionEvent(
        thought=[TextContent(text="I could run npm install lodahs")],
        reasoning_content="maybe npm install lodahs would help",
        summary="considering npm install lodahs",
        tool_name="bash",
        tool_call_id="test",
        tool_call=MessageToolCall(
            id="test",
            name="bash",
            arguments=json.dumps({"command": "ls /tmp"}),
            origin="completion",
        ),
        llm_response_id="test",
    )
    analyzer = SupplyChainSecurityAnalyzer()
    assert analyzer.security_risk(action) == SecurityRisk.LOW


def test_zero_width_evasion_still_detected():
    """A zero-width char hiding the typosquat name is normalized away first."""
    analyzer = SupplyChainSecurityAnalyzer()
    # lod<ZWSP>ahs -> lodahs after normalization -> flagged as lodash.
    assert (
        analyzer.security_risk(make_action("npm install lod​ahs")) == SecurityRisk.HIGH
    )


def test_fullwidth_evasion_still_detected():
    """Fullwidth glyphs in the command are NFKC-folded before parsing."""
    analyzer = SupplyChainSecurityAnalyzer()
    # Fullwidth "npm install lodahs" folds to ascii then flags lodash.
    cmd = "ｎｐｍ install lodahs"
    assert analyzer.security_risk(make_action(cmd)) == SecurityRisk.HIGH


# ---------------------------------------------------------------------------
# Line-separator / invisible evasion regression
#
# U+0085 (NEL), U+2028 (line separator), U+2029 (paragraph separator) and a
# zero-width space previously split a package name when the old analyzer ran
# str.splitlines() before stripping invisibles. Normalization now strips them in
# the parser entry, so each of these still flags HIGH.
# ---------------------------------------------------------------------------

_INVISIBLE_EVASION_CASES = [
    ("npm install lod\u0085ahs", "U+0085 NEL split"),
    ("npm install lod\u2028ahs", "U+2028 line separator split"),
    ("npm install lod\u2029ahs", "U+2029 paragraph separator split"),
    ("npm install load\u200bsh", "zero-width split of loadsh"),
    ("ｎｐｍ install load\u200bsh", "fullwidth npm plus zero-width split"),
]


@pytest.mark.parametrize(
    "command,desc",
    _INVISIBLE_EVASION_CASES,
    ids=[c[1] for c in _INVISIBLE_EVASION_CASES],
)
def test_analyzer_invisible_evasion_high(command: str, desc: str):
    analyzer = SupplyChainSecurityAnalyzer()
    risk = analyzer.security_risk(make_action(command))
    assert risk == SecurityRisk.HIGH, f"{desc}: expected HIGH, got {risk}"


def test_parser_entry_normalizes_zero_width_directly():
    """The public parser entry normalizes too, not just the analyzer.

    Calling find_typosquat_installs directly with a zero-width-split name must
    still surface the typosquat, proving the entry point is not evadable.
    """
    from openhands.sdk.security.supply_chain.parser import find_typosquat_installs

    findings = find_typosquat_installs("npm install lod​ahs")
    assert findings, "expected the zero-width-split install to be flagged"
    assert any(f.suggestion == "lodash" for f in findings)


def test_analyzer_handles_non_command_arguments():
    """A tool whose arguments are not a shell command must not crash or flag."""
    action = ActionEvent(
        thought=[TextContent(text="test")],
        tool_name="file_editor",
        tool_call_id="test",
        tool_call=MessageToolCall(
            id="test",
            name="file_editor",
            arguments=json.dumps({"path": "/tmp/x", "content": "lodahs"}),
            origin="completion",
        ),
        llm_response_id="test",
    )
    analyzer = SupplyChainSecurityAnalyzer()
    # "lodahs" appears as file content, not an install command -- stays LOW.
    assert analyzer.security_risk(action) == SecurityRisk.LOW


# ---------------------------------------------------------------------------
# Serialization: analyzer is a DiscriminatedUnion model like its siblings
# ---------------------------------------------------------------------------


def test_analyzer_round_trips_through_serialization():
    analyzer = SupplyChainSecurityAnalyzer()
    dumped = analyzer.model_dump_json()
    restored = SupplyChainSecurityAnalyzer.model_validate_json(dumped)
    assert isinstance(restored, SupplyChainSecurityAnalyzer)
    assert restored.security_risk(make_action("npm install lodahs")) == (
        SecurityRisk.HIGH
    )


def test_exported_from_security_package():
    from openhands.sdk.security import (
        SupplyChainSecurityAnalyzer as Exported,
    )

    assert Exported is SupplyChainSecurityAnalyzer


# ---------------------------------------------------------------------------
# AST-structural cases (the extraction upgrade onto the shared shell view)
# ---------------------------------------------------------------------------

_AST_HIGH_CASES = [
    ("/bin/npm install loadsh", "path-qualified manager via posix basename"),
    ("true && npm i loadsh", "&& list traversal"),
    ("echo done; npm i loadsh", "; traversal"),
    ("npx loadsh", "runner via ast"),
    ("cat foo || npm install loadsh", "|| traversal"),
    ("npm install   loadsh", "collapsed odd spacing"),
    ("npm install 'loadsh'", "single-quote raw_string recovery"),
    ('npm install "loadsh"', "double-quote string recovery"),
    ("echo $(npm install loadsh)", "command substitution nested command"),
    ('npm install lodahs "unterminated', "parse error still yields operand"),
]


@pytest.mark.parametrize(
    "command,desc", _AST_HIGH_CASES, ids=[c[1] for c in _AST_HIGH_CASES]
)
def test_analyzer_ast_high(command: str, desc: str):
    analyzer = SupplyChainSecurityAnalyzer()
    risk = analyzer.security_risk(make_action(command))
    assert risk == SecurityRisk.HIGH, f"{desc}: expected HIGH, got {risk}"


_AST_LOW_CASES = [
    ('echo "npm install lodahs"', "whole install inside a double-quoted string"),
    ('bash -c "npm install lodahs"', "bash -c inner string is opaque"),
    ("$(echo npm) install lodahs", "opaque outer command name skipped"),
]


@pytest.mark.parametrize(
    "command,desc", _AST_LOW_CASES, ids=[c[1] for c in _AST_LOW_CASES]
)
def test_analyzer_ast_low(command: str, desc: str):
    analyzer = SupplyChainSecurityAnalyzer()
    risk = analyzer.security_risk(make_action(command))
    assert risk == SecurityRisk.LOW, f"{desc}: expected LOW, got {risk}"


def test_lone_surrogate_command_does_not_raise():
    """A crafted lone-surrogate command is guarded; analyzer returns LOW.

    The strict UTF-8 encode in the parser would raise UnicodeEncodeError on a
    lone surrogate; the analyzer catches it so it never raises out of the
    security seam (where the ensemble would otherwise fail closed to HIGH).
    """
    analyzer = SupplyChainSecurityAnalyzer()
    cmd = "npm install lodahs\ud800"  # lone high surrogate
    assert analyzer.security_risk(make_action(cmd)) == SecurityRisk.LOW


# ---------------------------------------------------------------------------
# Realistic terminal ActionEvent: carries a typed action AND a tool call; the
# analyzer reads the command from the tool-call arguments.
# ---------------------------------------------------------------------------


def _terminal_action_event(command: str) -> ActionEvent:
    """Build a realistic terminal ActionEvent (typed action + tool call)."""
    from openhands.tools.terminal.definition import TerminalAction

    return ActionEvent(
        thought=[TextContent(text="test")],
        action=TerminalAction(command=command),
        tool_name="terminal",
        tool_call_id="test",
        tool_call=MessageToolCall(
            id="test",
            name="terminal",
            arguments=json.dumps({"command": command}),
            origin="completion",
        ),
        llm_response_id="test",
    )


def test_typed_terminal_action_high():
    analyzer = SupplyChainSecurityAnalyzer()
    action = _terminal_action_event("npm install lodahs")
    assert analyzer.security_risk(action) == SecurityRisk.HIGH


def test_typed_terminal_action_low():
    analyzer = SupplyChainSecurityAnalyzer()
    action = _terminal_action_event("npm install lodash")
    assert analyzer.security_risk(action) == SecurityRisk.LOW


# ---------------------------------------------------------------------------
# Interior-quote / backslash operand obfuscation at the action boundary
#
# These all resolve to the single argv token `loadsh` (a typosquat of `lodash`)
# in a real shell. The bounded operand-literal decode reconstructs that token,
# so they raise HIGH; the runtime/split variants stay opaque and LOW.
# ---------------------------------------------------------------------------

_OBFUSCATION_HIGH_CASES = [
    ('npm install lo""adsh', "empty double-quote middle"),
    ('npm install load"sh"', "trailing double-quoted segment"),
    ("npm install lo'adsh'", "trailing single-quoted segment"),
    ("npm install lo\\adsh", "single backslash escape"),
    ('npm install l"o"a"d"s"h"', "alternating double-quoted chars"),
    ("npm install lo''adsh", "empty single-quote middle"),
    ("npm install l\\o\\a\\d\\s\\h", "every char backslash-escaped"),
    ('npm install "load"sh', "leading quoted then bare concat"),
]


@pytest.mark.parametrize(
    "command,desc",
    _OBFUSCATION_HIGH_CASES,
    ids=[c[1] for c in _OBFUSCATION_HIGH_CASES],
)
def test_analyzer_obfuscated_operand_high(command: str, desc: str):
    analyzer = SupplyChainSecurityAnalyzer()
    risk = analyzer.security_risk(make_action(command))
    assert risk == SecurityRisk.HIGH, f"{desc}: expected HIGH, got {risk}"


_OBFUSCATION_LOW_CASES = [
    ("npm install $(echo loadsh)", "command substitution stays opaque"),
    ("npm install `echo loadsh`", "backticks stay opaque"),
    ("npm install $CMD", "bare variable stays opaque"),
    ('npm install "a b"', "embedded space is not one package name"),
    ('npm install lo"$x"adsh', "variable inside quoted segment opaque"),
]


@pytest.mark.parametrize(
    "command,desc",
    _OBFUSCATION_LOW_CASES,
    ids=[c[1] for c in _OBFUSCATION_LOW_CASES],
)
def test_analyzer_obfuscated_operand_low(command: str, desc: str):
    analyzer = SupplyChainSecurityAnalyzer()
    risk = analyzer.security_risk(make_action(command))
    assert risk == SecurityRisk.LOW, f"{desc}: expected LOW, got {risk}"


# ---------------------------------------------------------------------------
# Command-name obfuscation, ANSI-C operands, and line continuation (boundary)
#
# Same evasions exercised in the parser tests, asserted at the analyzer
# boundary: an obfuscated manager/runner NAME (`n"p"m`, `\npm`, `$'npm'`), an
# ANSI-C quoted operand (`$'loadsh'`, `$'loa\x64sh'`), and a backslash-newline
# line continuation all run a real `npm install loadsh`/`npx loadsh`, so they
# raise HIGH; runtime expansions and single-quote-preserved continuations stay
# opaque and LOW.
# ---------------------------------------------------------------------------

_NAME_AND_ENCODING_HIGH_CASES = [
    ('n"p"m install loadsh', "interior-quoted manager name"),
    (r"\npm install loadsh", "backslash-escaped manager name"),
    ("$'npm' install loadsh", "ANSI-C manager name"),
    ('n"p"x loadsh', "interior-quoted runner name"),
    ("$'npx' loadsh", "ANSI-C runner name"),
    ("npm install $'loadsh'", "ANSI-C operand"),
    (r"npm install $'loa\x64sh'", "ANSI-C hex-escape operand"),
    ("npm install loa\\\ndsh", "operand line continuation"),
    ("np\\\nm install loadsh", "command-name line continuation"),
]


@pytest.mark.parametrize(
    "command,desc",
    _NAME_AND_ENCODING_HIGH_CASES,
    ids=[c[1] for c in _NAME_AND_ENCODING_HIGH_CASES],
)
def test_analyzer_name_and_encoding_high(command: str, desc: str):
    analyzer = SupplyChainSecurityAnalyzer()
    risk = analyzer.security_risk(make_action(command))
    assert risk == SecurityRisk.HIGH, f"{desc}: expected HIGH, got {risk}"


_NAME_AND_ENCODING_LOW_CASES = [
    ("$CMD install loadsh", "runtime variable command name stays opaque"),
    ("$(echo npm) install loadsh", "command-substitution name stays opaque"),
    (r"npm install $'a b'", "ANSI-C with embedded space stays opaque"),
    (r"npm install $'lo\tadsh'", "ANSI-C with control char stays opaque"),
    ("npm install 'loa\\\ndsh'", "single-quoted line continuation is literal"),
    ("$'npm' install lodash", "ANSI-C name of a legitimate install stays LOW"),
]


@pytest.mark.parametrize(
    "command,desc",
    _NAME_AND_ENCODING_LOW_CASES,
    ids=[c[1] for c in _NAME_AND_ENCODING_LOW_CASES],
)
def test_analyzer_name_and_encoding_low(command: str, desc: str):
    analyzer = SupplyChainSecurityAnalyzer()
    risk = analyzer.security_risk(make_action(command))
    assert risk == SecurityRisk.LOW, f"{desc}: expected LOW, got {risk}"


# ---------------------------------------------------------------------------
# Documented residue (strict xfails): nested command-string payloads
#
# When the whole install lives inside an inner command string -- `bash -c
# "npm install lodahs"`, `sh -c 'npm i loadsh'`, `echo "npm install lodahs"` --
# the tree-sitter view treats that inner string as a single opaque argument to
# the OUTER command (bash/sh/echo). It is never re-parsed as a command, so the
# install is invisible to the analyzer and stays LOW (the safe default).
#
# Closing these needs recursive command-string parsing: recognising `-c`/`-lc`
# shells (and `echo`-style sinks) and re-parsing their string argument as a
# nested program. That is out of scope for the current shared AST view; it is
# the same recursion the #2721 migration scopes for the pattern analyzer. These
# are marked xfail(strict=True) so they flip to passing -- and fail loudly as
# stale -- the moment that recursion lands.
# ---------------------------------------------------------------------------

_NESTED_COMMAND_STRING_RESIDUE = [
    ('bash -c "npm install lodahs"', "bash_dash_c_double_quoted"),
    ("sh -c 'npm i loadsh'", "sh_dash_c_single_quoted"),
    ('echo "npm install lodahs"', "echo_double_quoted_install"),
]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Install lives inside an inner command string; the AST treats it as one"
        " opaque argument to the outer bash/sh/echo. Needs recursive"
        " command-string parsing (re-parse the -c / echoed string as a nested"
        " program) -- out of scope for the current shared AST view; cf. the"
        " #2721 migration scope."
    ),
)
@pytest.mark.parametrize(
    "command,desc",
    _NESTED_COMMAND_STRING_RESIDUE,
    ids=[c[1] for c in _NESTED_COMMAND_STRING_RESIDUE],
)
def test_analyzer_nested_command_string_residue_xfail(command: str, desc: str):
    analyzer = SupplyChainSecurityAnalyzer()
    # Aspirational: the inner install SHOULD raise HIGH once command strings are
    # parsed recursively. Today it stays LOW (opaque), so this xfails.
    risk = analyzer.security_risk(make_action(command))
    assert risk == SecurityRisk.HIGH


# ---------------------------------------------------------------------------
# Pathologically nested / chained input never crashes the seam (DoS hardening)
#
# Hundreds of nested `$()` levels or chained operators build a parse tree deep
# enough that the recursive tree-sitter-bash walkers raise RecursionError. The
# analyzer widens its fail-open guard (alongside UnicodeEncodeError) to catch it
# and return LOW, so adversarial noise is a documented non-finding, never an
# exception escaping the security seam (where the ensemble would fail closed to
# HIGH). A real install in a moderately-chained command still raises HIGH.
# ---------------------------------------------------------------------------

_PATHOLOGICAL_LOW_CASES = [
    (" && ".join("ls" for _ in range(1500)), "deeply_chained_operators"),
    ("x" + "$(" * 200 + "echo" + ")" * 200, "deeply_nested_command_substitution"),
]


@pytest.mark.parametrize(
    "command,desc",
    _PATHOLOGICAL_LOW_CASES,
    ids=[c[1] for c in _PATHOLOGICAL_LOW_CASES],
)
def test_pathological_input_does_not_raise_and_stays_low(command: str, desc: str):
    analyzer = SupplyChainSecurityAnalyzer()
    # Must not raise RecursionError; treated as a non-finding -> LOW.
    risk = analyzer.security_risk(make_action(command))
    assert risk == SecurityRisk.LOW, f"{desc}: expected LOW, got {risk}"


def test_moderately_chained_real_install_still_high():
    # The widened guard must not suppress real findings: a typosquat install
    # behind a normal chain still raises HIGH.
    analyzer = SupplyChainSecurityAnalyzer()
    assert analyzer.security_risk(make_action("true && npm i loadsh")) == (
        SecurityRisk.HIGH
    )
    assert (
        analyzer.security_risk(make_action("a && b && npm install loadsh"))
        == SecurityRisk.HIGH
    )
