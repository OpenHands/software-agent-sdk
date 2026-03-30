"""Deterministic, local security analyzers for agent action boundaries.

Three analyzers, each owning one job:

- ``PatternSecurityAnalyzer`` -- regex signatures with two-corpus scanning
- ``PolicyRailSecurityAnalyzer`` -- composed-condition rules (fetch-to-exec, etc.)
- ``EnsembleSecurityAnalyzer`` -- pure combiner via max-severity fusion

Wire them into a conversation alongside ``ConfirmRisky`` to classify
agent actions before execution. No network calls, no model inference,
no dependencies beyond the SDK runtime.
"""

from openhands.sdk.security.defense_in_depth.ensemble import EnsembleSecurityAnalyzer
from openhands.sdk.security.defense_in_depth.pattern import PatternSecurityAnalyzer
from openhands.sdk.security.defense_in_depth.policy_rails import (
    PolicyRailSecurityAnalyzer,
)


__all__ = [
    "PatternSecurityAnalyzer",
    "PolicyRailSecurityAnalyzer",
    "EnsembleSecurityAnalyzer",
]
