"""Combine multiple security analyzers into a single risk assessment.

If you have a ``PatternSecurityAnalyzer`` catching known signatures and
a ``PolicyRailSecurityAnalyzer`` catching composed threats, you want one
answer: what is the worst-case risk across all of them? That is what
this module does -- pure fusion, no detection of its own.
"""

from __future__ import annotations

from pydantic import Field

from openhands.sdk.event import ActionEvent
from openhands.sdk.logger import get_logger
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.risk import SecurityRisk


logger = get_logger(__name__)


class EnsembleSecurityAnalyzer(SecurityAnalyzerBase):
    """Wire multiple analyzers together and take the worst-case risk.

    Use this as the top-level analyzer you set on a conversation. It
    calls each child analyzer, collects their risk assessments, and
    returns the highest concrete risk. It does not perform any detection,
    extraction, or normalization of its own.

    How UNKNOWN works: if *all* children return UNKNOWN, the ensemble
    returns UNKNOWN (which ``ConfirmRisky`` confirms by default). If any
    child returns a concrete level, UNKNOWN results are filtered out and
    the highest concrete level wins. UNKNOWN is never passed to ``max()``
    -- that would raise ``ValueError`` by design.

    If a child analyzer raises an exception, it contributes HIGH
    (fail-closed, logged). This prevents a broken analyzer from silently
    degrading safety.

    Example::

        from openhands.sdk.security import (
            EnsembleSecurityAnalyzer,
            PatternSecurityAnalyzer,
            PolicyRailSecurityAnalyzer,
            ConfirmRisky,
            SecurityRisk,
        )

        analyzer = EnsembleSecurityAnalyzer(
            analyzers=[
                PolicyRailSecurityAnalyzer(),
                PatternSecurityAnalyzer(),
            ]
        )
        policy = ConfirmRisky(threshold=SecurityRisk.MEDIUM)
    """

    analyzers: list[SecurityAnalyzerBase] = Field(
        ...,
        description="Analyzers whose assessments are combined via max-severity",
        min_length=1,
    )

    def security_risk(self, action: ActionEvent) -> SecurityRisk:
        """Evaluate risk via max-severity fusion across child analyzers."""
        results: list[SecurityRisk] = []
        for analyzer in self.analyzers:
            try:
                results.append(analyzer.security_risk(action))
            except Exception:
                logger.exception("Analyzer %s raised -- fail-closed to HIGH", analyzer)
                results.append(SecurityRisk.HIGH)

        # Partition: concrete risks vs UNKNOWN
        concrete = [r for r in results if r != SecurityRisk.UNKNOWN]

        if not concrete:
            return SecurityRisk.UNKNOWN

        # max() uses SecurityRisk.__lt__; UNKNOWN already filtered out.
        return max(concrete)
