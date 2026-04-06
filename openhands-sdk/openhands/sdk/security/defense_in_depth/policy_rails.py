"""Block obviously dangerous composed actions before pattern scanning runs.

Some threats are structural, not lexical: ``curl ... | bash`` is
dangerous because of the *combination* of fetch + pipe-to-exec, not
because either token is dangerous alone. Rails express these composed
conditions as deterministic rules evaluated per-segment, so that
tokens from different fields (thought vs. tool arguments) cannot
accidentally satisfy a composed condition.

v1 ships three rails: fetch-to-exec, raw-disk-op, catastrophic-delete.
Each rail maps to ``SecurityRisk.HIGH`` at the SDK boundary. The
confirmation policy decides whether to prompt the user.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from openhands.sdk.event import ActionEvent
from openhands.sdk.logger import get_logger
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.defense_in_depth.utils import (
    _extract_exec_segments,
    _normalize,
)
from openhands.sdk.security.risk import SecurityRisk


logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Stable rail IDs -- do not change between releases without documentation.
# ---------------------------------------------------------------------------

RAIL_FETCH_TO_EXEC = "fetch-to-exec"
RAIL_RAW_DISK_OP = "raw-disk-op"
RAIL_CATASTROPHIC_DELETE = "catastrophic-delete"


# ---------------------------------------------------------------------------
# Rail types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RailDecision:
    """Result of a policy rail evaluation.

    ``outcome`` is a ``SecurityRisk`` level: ``HIGH`` when a rail fires,
    ``LOW`` when all rails pass. ``reason`` preserves observability for
    logging and debugging.
    """

    outcome: SecurityRisk
    rule_name: str = ""
    reason: str = ""


_PASS = RailDecision(outcome=SecurityRisk.LOW)


# ---------------------------------------------------------------------------
# Rail evaluation
# ---------------------------------------------------------------------------


def _evaluate_rail_segments(segments: list[str]) -> RailDecision:
    """Evaluate deterministic policy rails against per-segment content.

    Per-segment evaluation prevents cross-field false positives: composed
    conditions like "curl + pipe to sh" require both tokens in the same
    segment. An agent whose thought mentions "curl" and whose tool call
    runs "ls" would falsely trigger a flat-string check.
    """
    ci = re.IGNORECASE

    for seg in segments:
        has_fetch = bool(re.search(r"\b(?:curl|wget)\b", seg, ci))
        has_pipe_to_exec = bool(
            re.search(
                r"\|\s*(?:ba)?sh\b|\|\s*python[23]?\b|\|\s*perl\b|\|\s*ruby\b",
                seg,
                ci,
            )
        )
        has_recursive_force = bool(
            re.search(
                r"\brm\b(?:(?![;&|]).){0,120}"
                r"(?:(?:--recursive\b|-[^\s-]*[rR][^\s]*)"
                r"(?:(?![;&|]).){0,120}(?:--force\b|-[^\s-]*f[^\s]*)"
                r"|(?:--force\b|-[^\s-]*f[^\s]*)"
                r"(?:(?![;&|]).){0,120}(?:--recursive\b|-[^\s-]*[rR][^\s]*))",
                seg,
                ci,
            )
        )

        # Rule 1: fetch-to-exec -- download piped to shell/interpreter
        if has_fetch and has_pipe_to_exec:
            return RailDecision(
                SecurityRisk.HIGH,
                RAIL_FETCH_TO_EXEC,
                "Network fetch piped to shell/interpreter",
            )

        # Rule 2: raw-disk-op -- dd to device or mkfs
        if re.search(r"\bdd\b.{0,100}of=/dev/", seg, ci):
            return RailDecision(
                SecurityRisk.HIGH, RAIL_RAW_DISK_OP, "Raw disk write via dd"
            )
        if re.search(r"\bmkfs\.", seg, ci):
            return RailDecision(
                SecurityRisk.HIGH, RAIL_RAW_DISK_OP, "Filesystem format via mkfs"
            )

        # Rule 3: catastrophic-delete -- recursive force-delete of critical targets
        if has_recursive_force:
            critical = re.search(
                r"\brm\b.{0,60}\s(?:/(?:\s|$|\*)"
                r"|~/?(?:\s|$)"
                r"|/(?:etc|usr|var|home|boot)\b)",
                seg,
                ci,
            )
            if critical:
                return RailDecision(
                    SecurityRisk.HIGH,
                    RAIL_CATASTROPHIC_DELETE,
                    "Recursive force-delete targeting critical path",
                )

    return _PASS


def _evaluate_rail(content: str) -> RailDecision:
    """Evaluate rails against a single string (convenience wrapper).

    Normalizes the content before evaluation so callers do not need
    to remember to pre-normalize. This matches the behavior of
    PolicyRailSecurityAnalyzer.security_risk().
    """
    return _evaluate_rail_segments([_normalize(content)])


# ---------------------------------------------------------------------------
# PolicyRailSecurityAnalyzer
# ---------------------------------------------------------------------------


class PolicyRailSecurityAnalyzer(SecurityAnalyzerBase):
    """Catch composed threats that plain regex signatures would miss.

    Use this when you need to detect threats defined by *combinations*
    of tokens (e.g., ``curl`` piped to ``bash``) rather than individual
    signatures. While these rails *could* each be expressed as a single
    regex, keeping them as named rules with per-segment evaluation makes
    the threat model more interpretable, the rules easier to maintain,
    and the audit trail clearer than a flat pattern list.

    Evaluates normalized executable segments only -- reasoning text is
    never scanned.

    Returns ``SecurityRisk.HIGH`` when a rail fires, ``LOW`` otherwise.
    Pair with ``ConfirmRisky`` and compose via ``EnsembleSecurityAnalyzer``.

    v1 rails: fetch-to-exec, raw-disk-op, catastrophic-delete.

    Example::

        from openhands.sdk.security import PolicyRailSecurityAnalyzer

        analyzer = PolicyRailSecurityAnalyzer()
        # risk = analyzer.security_risk(action)
    """

    def security_risk(self, action: ActionEvent) -> SecurityRisk:
        """Evaluate policy rails on normalized executable segments."""
        segments = [_normalize(s) for s in _extract_exec_segments(action)]
        rail = _evaluate_rail_segments(segments)
        if rail.outcome != SecurityRisk.LOW:
            logger.debug(
                "Policy rail fired: %s (%s) -> HIGH",
                rail.rule_name,
                rail.reason,
            )
            return SecurityRisk.HIGH
        return SecurityRisk.LOW
