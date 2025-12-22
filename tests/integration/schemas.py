"""
JSON schemas for structured integration test results.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


def json_serializer(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


class TokenUsageData(BaseModel):
    """Token usage data for a test instance."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0

    def __add__(self, other: "TokenUsageData") -> "TokenUsageData":
        """Add two TokenUsageData instances together."""
        return TokenUsageData(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )


class TestResultData(BaseModel):
    """Individual test result data."""

    success: bool
    reason: str | None = None
    skipped: bool = False


class TestInstanceResult(BaseModel):
    """Result from a single test instance."""

    instance_id: str
    test_result: TestResultData
    test_type: Literal["integration", "behavior"]
    required: bool  # True for integration tests, False for behavior tests
    cost: float = 0.0
    token_usage: TokenUsageData | None = None
    error_message: str | None = None


class ModelTestResults(BaseModel):
    """Complete test results for a single model."""

    # Metadata
    model_name: str
    run_suffix: str
    llm_config: dict[str, Any]
    timestamp: datetime = Field(default_factory=datetime.now)

    # Test execution data
    test_instances: list[TestInstanceResult]

    # Summary statistics
    total_tests: int
    successful_tests: int
    skipped_tests: int
    success_rate: float
    total_cost: float
    total_token_usage: TokenUsageData | None = None

    # Type-specific statistics
    integration_tests_total: int = 0
    integration_tests_successful: int = 0
    integration_tests_success_rate: float = 0.0
    behavior_tests_total: int = 0
    behavior_tests_successful: int = 0
    behavior_tests_success_rate: float = 0.0

    # Additional metadata
    eval_note: str | None = None
    artifact_url: str | None = None
    status: str = "completed"

    @classmethod
    def from_eval_outputs(
        cls,
        eval_outputs: list[Any],  # list[EvalOutput]
        model_name: str,
        run_suffix: str,
        llm_config: dict[str, Any],
        eval_note: str | None = None,
        artifact_url: str | None = None,
    ) -> "ModelTestResults":
        """Create ModelTestResults from list of EvalOutput objects."""

        # Convert EvalOutput objects to TestInstanceResult
        test_instances = []
        for output in eval_outputs:
            # Convert token usage if available
            token_usage = None
            if output.token_usage is not None:
                token_usage = TokenUsageData(
                    prompt_tokens=output.token_usage.prompt_tokens,
                    completion_tokens=output.token_usage.completion_tokens,
                    cache_read_tokens=output.token_usage.cache_read_tokens,
                    cache_write_tokens=output.token_usage.cache_write_tokens,
                    reasoning_tokens=output.token_usage.reasoning_tokens,
                )

            test_instances.append(
                TestInstanceResult(
                    instance_id=output.instance_id,
                    test_result=TestResultData(
                        success=output.test_result.success,
                        reason=output.test_result.reason,
                        skipped=output.test_result.skipped,
                    ),
                    test_type=output.test_type,
                    required=output.required,
                    cost=output.cost,
                    token_usage=token_usage,
                    error_message=output.error_message,
                )
            )

        # Calculate summary statistics
        total_tests = len(test_instances)
        successful_tests = sum(1 for t in test_instances if t.test_result.success)
        skipped_tests = sum(1 for t in test_instances if t.test_result.skipped)
        # Exclude skipped tests from success rate calculation
        non_skipped_tests = total_tests - skipped_tests
        success_rate = (
            successful_tests / non_skipped_tests if non_skipped_tests > 0 else 0.0
        )
        total_cost = sum(t.cost for t in test_instances)

        # Calculate total token usage
        total_token_usage = TokenUsageData()
        for t in test_instances:
            if t.token_usage is not None:
                total_token_usage = total_token_usage + t.token_usage

        # Calculate type-specific statistics
        integration_tests = [t for t in test_instances if t.test_type == "integration"]
        behavior_tests = [t for t in test_instances if t.test_type == "behavior"]

        integration_tests_total = len(integration_tests)
        integration_tests_successful = sum(
            1 for t in integration_tests if t.test_result.success
        )
        integration_skipped = sum(1 for t in integration_tests if t.test_result.skipped)
        integration_non_skipped = integration_tests_total - integration_skipped
        integration_tests_success_rate = (
            integration_tests_successful / integration_non_skipped
            if integration_non_skipped > 0
            else 0.0
        )

        behavior_tests_total = len(behavior_tests)
        behavior_tests_successful = sum(
            1 for t in behavior_tests if t.test_result.success
        )
        behavior_skipped = sum(1 for t in behavior_tests if t.test_result.skipped)
        behavior_non_skipped = behavior_tests_total - behavior_skipped
        behavior_tests_success_rate = (
            behavior_tests_successful / behavior_non_skipped
            if behavior_non_skipped > 0
            else 0.0
        )

        return cls(
            model_name=model_name,
            run_suffix=run_suffix,
            llm_config=llm_config,
            test_instances=test_instances,
            total_tests=total_tests,
            successful_tests=successful_tests,
            skipped_tests=skipped_tests,
            success_rate=success_rate,
            total_cost=total_cost,
            total_token_usage=total_token_usage,
            integration_tests_total=integration_tests_total,
            integration_tests_successful=integration_tests_successful,
            integration_tests_success_rate=integration_tests_success_rate,
            behavior_tests_total=behavior_tests_total,
            behavior_tests_successful=behavior_tests_successful,
            behavior_tests_success_rate=behavior_tests_success_rate,
            eval_note=eval_note,
            artifact_url=artifact_url,
        )


class ConsolidatedResults(BaseModel):
    """Consolidated results from all models."""

    # Metadata
    timestamp: datetime = Field(default_factory=datetime.now)
    total_models: int

    # Individual model results
    model_results: list[ModelTestResults]

    # Overall statistics
    overall_success_rate: float
    total_cost_all_models: float
    # Note: We intentionally don't aggregate token usage across models because
    # different models use different tokenizers, making cross-model token sums
    # meaningless. Per-model token usage is available in model_results.

    @classmethod
    def from_model_results(
        cls, model_results: list[ModelTestResults]
    ) -> "ConsolidatedResults":
        """Create ConsolidatedResults from list of ModelTestResults."""

        total_models = len(model_results)

        # Calculate overall statistics
        total_tests_all = sum(r.total_tests for r in model_results)
        total_successful_all = sum(r.successful_tests for r in model_results)
        total_skipped_all = sum(r.skipped_tests for r in model_results)
        # Exclude skipped tests from overall success rate calculation
        non_skipped_tests_all = total_tests_all - total_skipped_all
        overall_success_rate = (
            total_successful_all / non_skipped_tests_all
            if non_skipped_tests_all > 0
            else 0.0
        )
        total_cost_all_models = sum(r.total_cost for r in model_results)

        return cls(
            total_models=total_models,
            model_results=model_results,
            overall_success_rate=overall_success_rate,
            total_cost_all_models=total_cost_all_models,
        )
