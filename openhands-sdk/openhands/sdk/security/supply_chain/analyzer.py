"""Raise the risk of npm typosquat installs at the action boundary.

``PatternSecurityAnalyzer`` catches what a command *does* lexically (rm -rf,
curl | sh). It does not catch a command that runs a perfectly ordinary
``npm install`` of a package whose name is one keystroke off a popular one --
``lodahs`` instead of ``lodash``. That is a supply-chain attack the other
deterministic analyzers in this seam miss, because the danger is in the
*identity* of the dependency, not the shape of the command.

``SupplyChainSecurityAnalyzer`` closes that gap. It reads the command string from
the action's tool call (the ``"command"`` key of the raw tool-call arguments)
and hands it to
``find_typosquat_installs``, which normalizes the command to defeat
encoding-evasion and parses it offline with the shared tree-sitter-bash view (no
network, no disk). It returns ``SecurityRisk.HIGH`` when an install/runner
target is one edit away from a curated popular package. It never hard-denies;
pairing it with ``ConfirmRisky`` asks a human to make the call.

Unlike the pattern/policy analyzers in ``defense_in_depth``, this analyzer does
not import their private extraction/normalization helpers. It scans ONLY the
command string (never tool name, thought, reasoning, or summary), which keeps
the two-corpus invariant -- a typosquat mentioned only in reasoning stays LOW.
Encoding-evasion normalization (zero-width/fullwidth/bidi defeat) lives in the
parser entry point, so both this analyzer and direct parser callers share it.
"""

from __future__ import annotations

import json

from openhands.sdk.event import ActionEvent
from openhands.sdk.logger import get_logger
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.risk import SecurityRisk
from openhands.sdk.security.supply_chain.parser import find_typosquat_installs


logger = get_logger(__name__)


def _command_string(action: ActionEvent) -> str:
    """Extract the shell command string from an action's tool call.

    Reads the raw tool-call ``arguments`` JSON and returns its ``"command"``
    key. Returns ``""`` when there is no non-empty command string -- the
    analyzer then stays LOW. Only the command string is scanned; tool name,
    thought, reasoning and summary are never read, which preserves the
    two-corpus invariant.
    """
    tool_call = action.tool_call
    if tool_call is not None and tool_call.arguments:
        try:
            parsed = json.loads(tool_call.arguments)
        except (json.JSONDecodeError, TypeError):
            return ""
        if isinstance(parsed, dict):
            command = parsed.get("command")
            if isinstance(command, str):
                return command

    return ""


class SupplyChainSecurityAnalyzer(SecurityAnalyzerBase):
    """Flag npm-ecosystem typosquat installs so a human can approve them.

    Use this when an agent can run shell commands and you want to catch the
    one-edit-off dependency install that pattern and policy-rail analyzers do
    not see. It returns ``SecurityRisk.HIGH`` on a likely typosquat and
    ``SecurityRisk.LOW`` otherwise -- it never blocks on its own. The check is
    deterministic and offline: it scans only the command string of the action
    (the ``"command"`` key of the tool-call arguments), never the agent's
    reasoning text, so merely *mentioning* ``lodahs`` in a thought does not
    trip it.

    The extraction layer runs on the shared tree-sitter-bash command view, so
    command chaining, pipes, subshells and path-qualified managers parse
    structurally.

    Scope, stated honestly. The analyzer decodes the static obfuscations a real
    shell collapses to a fixed string before exec, and checks each result against
    the typosquat heuristic:

    - quote removal (``"lodahs"``, ``lo'adsh'``), backslash unescaping
      (``lo\\adsh``), interior-quote concatenation (``lo""adsh``), ANSI-C
      ``$'...'`` escapes, line-continuation joining, and bounded static
      comma-brace expansion (``lo{a,}dsh`` -> ``loadsh``/``lodsh``, each checked).

    What stays LOW because it cannot be resolved statically:

    - runtime expansion -- shell variables (``$PKG``), command/process
      substitution (``$(...)``, ```...```, ``<(...)``) and arithmetic
      substitution: the installed name is unknowable offline;
    - homoglyphs / Unicode confusables: only invisible/zero-width characters and
      fullwidth NFKC folds are normalized, not look-alike letters;
    - an install nested inside an inner command string (``bash -c '...'``,
      ``echo '...'``): treated as one opaque argument to the outer command (the
      documented xfail residue), pending recursive command-string parsing;
    - brace forms outside the bounded comma case: ranges (``{1..3}``), nested
      braces, and a brace touching a quote or variable stay opaque.

    It flags the typosquats it can prove statically and is explicit about what it
    does not catch; it never blocks on its own.

    Example::

        from openhands.sdk.security import (
            SupplyChainSecurityAnalyzer,
            ConfirmRisky,
            SecurityRisk,
        )

        analyzer = SupplyChainSecurityAnalyzer()
        policy = ConfirmRisky(threshold=SecurityRisk.HIGH)
    """

    def security_risk(self, action: ActionEvent) -> SecurityRisk:
        """Return ``HIGH`` if the command installs a likely typosquat, else
        ``LOW``.

        The command string is handed straight to ``find_typosquat_installs``,
        which normalizes it (stripping zero-width/bidi/format characters and
        NFKC-folding fullwidth glyphs) so an attacker cannot hide ``lodahs``
        behind an invisible character or fullwidth ``ｎｐｍ`` before parsing it on
        the shared tree-sitter view. A crafted lone-surrogate command can make
        the UTF-8 strict encode in the parser raise ``UnicodeEncodeError``, and a
        pathologically nested/chained command (hundreds of ``$()`` levels or
        chained operators) can make the recursive tree-sitter-bash walkers raise
        ``RecursionError``; both are caught and treated as LOW so the analyzer
        never raises out of the security seam. That adversarially-nested input is
        a known limitation: it is not analyzed and stays LOW, never a crash.
        """
        command = _command_string(action)
        if not command:
            return SecurityRisk.LOW

        try:
            findings = find_typosquat_installs(command)
        except (UnicodeEncodeError, RecursionError):
            # Intentional fail-open. UnicodeEncodeError: a lone-surrogate command
            # cannot be UTF-8 encoded to run, so there is no real install to
            # flag. RecursionError: pathologically nested/chained input (hundreds
            # of $() levels or chained operators) exhausts the recursion stack in
            # the shared parser's recursive walkers, so it is not analyzed (a
            # known limitation) rather than crashing the seam. Either way, there
            # is nothing to flag -- stay LOW.
            logger.debug(
                "Supply-chain check skipped: command is not UTF-8 encodable "
                "(lone surrogate) or too deeply nested/chained to parse; "
                "treating as LOW."
            )
            return SecurityRisk.LOW
        if findings:
            logger.debug(
                "Supply-chain typosquat flagged: %s",
                "; ".join(f.reason for f in findings),
            )
            return SecurityRisk.HIGH

        return SecurityRisk.LOW
