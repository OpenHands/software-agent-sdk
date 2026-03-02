"""Result types for API compliance tests."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class APIResponse(str, Enum):
    """Possible API response types for malformed input."""

    ACCEPTED = "accepted"
    """API processed the request (unexpected for malformed input)."""

    REJECTED = "rejected"
    """API returned an error (expected for malformed input)."""

    TIMEOUT = "timeout"
    """Request timed out."""

    CONNECTION_ERROR = "connection_error"
    """Could not connect to API."""


class ComplianceTestResult(BaseModel):
    """Result of a single compliance test run."""

    pattern_name: str = Field(description="Name of the malformed pattern tested")
    model: str = Field(description="Full model path (e.g., litellm_proxy/...)")
    model_id: str = Field(description="Short model ID for display (e.g., gpt-5.2)")
    provider: str = Field(description="Provider name (anthropic, openai, etc.)")
    response_type: APIResponse = Field(description="How the API responded")
    error_message: str | None = Field(
        default=None, description="Error message if rejected"
    )
    error_type: str | None = Field(
        default=None, description="Exception type name if rejected"
    )
    http_status: int | None = Field(default=None, description="HTTP status code")
    raw_response: dict[str, Any] | None = Field(
        default=None, description="Raw API response if accepted"
    )
    notes: str | None = Field(default=None, description="Additional notes")


class PatternResults(BaseModel):
    """Results for a single pattern across multiple models."""

    pattern_name: str
    pattern_description: str
    results: list[ComplianceTestResult] = Field(default_factory=list)

    def add_result(self, result: ComplianceTestResult) -> None:
        self.results.append(result)

    @property
    def rejected_count(self) -> int:
        return sum(1 for r in self.results if r.response_type == APIResponse.REJECTED)

    @property
    def accepted_count(self) -> int:
        return sum(1 for r in self.results if r.response_type == APIResponse.ACCEPTED)


class ComplianceReport(BaseModel):
    """Full compliance test report."""

    test_run_id: str = Field(description="Unique ID for this test run")
    timestamp: str = Field(description="ISO timestamp of test run")
    elapsed_time: float = Field(
        default=0.0, description="Total test duration in seconds"
    )
    patterns_tested: int = Field(description="Number of patterns tested")
    models_tested: list[str] = Field(description="List of models tested")
    results: list[PatternResults] = Field(default_factory=list)

    @property
    def total_tests(self) -> int:
        return sum(len(p.results) for p in self.results)

    @property
    def total_rejected(self) -> int:
        return sum(p.rejected_count for p in self.results)

    @property
    def total_accepted(self) -> int:
        return sum(p.accepted_count for p in self.results)
