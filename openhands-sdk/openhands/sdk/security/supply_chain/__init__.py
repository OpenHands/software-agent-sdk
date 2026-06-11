"""Deterministic, offline detection of npm-ecosystem supply-chain typosquats.

A sibling to ``defense_in_depth``: where that seam catches dangerous command
*shapes*, this one catches a dangerous dependency *identity* -- an install of a
package whose name is one edit away from a popular one.

- ``SupplyChainSecurityAnalyzer`` -- ``SecurityAnalyzerBase`` returning
  ``SecurityRisk.HIGH`` on a likely typosquat install, ``LOW`` otherwise.
- ``find_typosquat_installs`` -- the pure parser entry point, for callers that
  want the raw findings without the analyzer wrapper.

No network calls, no model inference, no dependencies beyond the SDK runtime.
"""

from openhands.sdk.security.supply_chain.analyzer import SupplyChainSecurityAnalyzer
from openhands.sdk.security.supply_chain.parser import (
    TyposquatFinding,
    find_typosquat_installs,
)


__all__ = [
    "SupplyChainSecurityAnalyzer",
    "find_typosquat_installs",
    "TyposquatFinding",
]
