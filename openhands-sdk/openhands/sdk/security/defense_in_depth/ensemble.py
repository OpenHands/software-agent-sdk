"""Ensemble security analyzer -- pure combiner via max-severity fusion.

Does not perform any detection, extraction, or normalization of its own.
Delegates to child analyzers and fuses results.
"""

from __future__ import annotations

from pydantic import Field

from openhands.sdk.event import ActionEvent
from openhands.sdk.logger import get_logger
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.risk import SecurityRisk


logger = get_logger(__name__)


class EnsembleSecurityAnalyzer(SecurityAnalyzerBase):
    """Combines multiple analyzers via max-severity fusion.

    Pure combiner: delegates to child analyzers and fuses results.
    Does not perform any detection, extraction, or normalization of its own.

    Fusion algorithm:
    1. Collect results from all child analyzers
    2. Partition into concrete (LOW, MEDIUM, HIGH) and UNKNOWN
    3. If any concrete results exist, return max(concrete)
    4. If all results are UNKNOWN, return UNKNOWN

    Never pass UNKNOWN to max() -- it raises ValueError by design.
    Analyzer exception -> HIGH (fail-closed, logged).
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
