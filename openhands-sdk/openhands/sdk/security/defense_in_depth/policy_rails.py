"""Deterministic segment-aware policy rails for composed-action threats.

Rails evaluate normalized executable segments only. Composed conditions
(e.g. curl + pipe to sh) require both tokens in the same segment to
prevent cross-field false positives.

v1 rails: fetch-to-exec, raw-disk-op, catastrophic-delete.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

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


class RailOutcome(Enum):
    """Internal policy recommendation from deterministic rail evaluation.

    DENY and CONFIRM both map to SecurityRisk.HIGH at the SDK boundary.
    The distinction is preserved internally for observability.
    """

    DENY = "DENY"
    CONFIRM = "CONFIRM"
    PASS = "PASS"


@dataclass(frozen=True)
class RailDecision:
    """Result of a policy rail evaluation."""

    outcome: RailOutcome
    rule_name: str = ""
    reason: str = ""


_PASS = RailDecision(outcome=RailOutcome.PASS)


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
                r"\brm\s+(?:-[frR]{2,}|-[rR]\s+-f|-f\s+-[rR]"
                r"|--recursive\s+--force|--force\s+--recursive)\b",
                seg,
                ci,
            )
        )

        # Rule 1: fetch-to-exec -- download piped to shell/interpreter
        if has_fetch and has_pipe_to_exec:
            return RailDecision(
                RailOutcome.DENY,
                RAIL_FETCH_TO_EXEC,
                "Network fetch piped to shell/interpreter",
            )

        # Rule 2: raw-disk-op -- dd to device or mkfs
        if re.search(r"\bdd\b.{0,100}of=/dev/", seg, ci):
            return RailDecision(
                RailOutcome.DENY, RAIL_RAW_DISK_OP, "Raw disk write via dd"
            )
        if re.search(r"\bmkfs\.", seg, ci):
            return RailDecision(
                RailOutcome.DENY, RAIL_RAW_DISK_OP, "Filesystem format via mkfs"
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
                    RailOutcome.DENY,
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
    """Deterministic segment-aware policy rails for composed-action threats.

    Evaluates normalized executable segments against structural rules
    that are stronger as composed conditions than plain regex signatures.

    v1 rails: fetch-to-exec, raw-disk-op, catastrophic-delete.
    """

    def security_risk(self, action: ActionEvent) -> SecurityRisk:
        """Evaluate policy rails on normalized executable segments."""
        segments = [_normalize(s) for s in _extract_exec_segments(action)]
        rail = _evaluate_rail_segments(segments)
        if rail.outcome != RailOutcome.PASS:
            logger.info(
                "Policy rail fired: %s (%s) -> HIGH",
                rail.rule_name,
                rail.reason,
            )
            return SecurityRisk.HIGH
        return SecurityRisk.LOW
