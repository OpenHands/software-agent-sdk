"""API Compliance monitoring for conversation events.

This module provides an APIComplianceMonitor that detects violations of LLM API
requirements in the event stream. Violations are currently logged for data
gathering; future versions may support reconciliation strategies.

The monitor enforces valid tool-call sequences:
- When tool calls are pending, only matching observations are allowed
- Messages cannot interleave with pending tool calls
- Tool results must reference known tool_call_ids
"""

from openhands.sdk.conversation.compliance.base import (
    ComplianceState,
    ComplianceViolation,
)
from openhands.sdk.conversation.compliance.monitor import APIComplianceMonitor


__all__ = [
    "APIComplianceMonitor",
    "ComplianceState",
    "ComplianceViolation",
]
