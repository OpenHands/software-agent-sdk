"""API Compliance Tests.

This module provides a framework for testing how different LLM APIs respond
to malformed message patterns. These tests are documentary in nature - they
intentionally send invalid data to understand API behavior across providers.

The tests are NON-BLOCKING: they are expected to fail and exist to document
API behavior, not enforce correctness.
"""

from tests.integration.api_compliance.base import BaseAPIComplianceTest
from tests.integration.api_compliance.result import APIResponse, ComplianceTestResult


__all__ = [
    "BaseAPIComplianceTest",
    "APIResponse",
    "ComplianceTestResult",
]
