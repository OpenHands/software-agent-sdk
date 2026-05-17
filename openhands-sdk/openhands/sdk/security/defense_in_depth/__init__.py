"""Deterministic, local security analyzers for agent action boundaries.

Two analyzers, each owning one job:

- ``PatternSecurityAnalyzer`` -- regex signatures with two-corpus scanning
- ``PolicyRailSecurityAnalyzer`` -- composed-condition rules (fetch-to-exec, etc.)

Wire them into a conversation alongside ``EnsembleSecurityAnalyzer`` and
``ConfirmRisky`` to classify agent actions before execution. No network
calls, no model inference, no dependencies beyond the SDK runtime.
"""

from openhands.sdk.security.defense_in_depth.pattern import PatternSecurityAnalyzer
from openhands.sdk.security.defense_in_depth.policy_rails import (
    PolicyRailSecurityAnalyzer,
)


__all__ = [
    "PatternSecurityAnalyzer",
    "PolicyRailSecurityAnalyzer",
]
