#!/usr/bin/env python3
"""
API Compliance Test Runner.

Runs malformed message pattern tests against multiple LLM providers
and generates a report documenting API behavior.

Usage:
    # Run all patterns against all models
    uv run python tests/integration/api_compliance/run_compliance.py

    # Run specific patterns
    uv run python tests/integration/api_compliance/run_compliance.py \
        --patterns unmatched_tool_use,interleaved_user_message

    # Run against specific models
    uv run python tests/integration/api_compliance/run_compliance.py \
        --models claude-sonnet-4-5-20250929,gpt-5.2

    # Output to specific directory
    uv run python tests/integration/api_compliance/run_compliance.py \
        --output-dir ./compliance-results
"""

import argparse
import importlib.util
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from openhands.sdk.logger import get_logger
from tests.integration.api_compliance.base import BaseAPIComplianceTest, create_test_llm
from tests.integration.api_compliance.result import (
    APIResponse,
    ComplianceReport,
    ComplianceTestResult,
    PatternResults,
)


logger = get_logger(__name__)

# Default models to test - mirrors the integration test matrix
DEFAULT_MODELS: dict[str, dict[str, Any]] = {
    "claude-sonnet-4-5": {
        "model": "litellm_proxy/claude-sonnet-4-5-20250929",
        "temperature": 0.0,
    },
    "gpt-5.2": {
        "model": "litellm_proxy/openai/gpt-5.2-2025-12-11",
    },
    "gemini-3-pro": {
        "model": "litellm_proxy/gemini-3-pro-preview",
    },
    "deepseek-v3.2": {
        "model": "litellm_proxy/deepseek/deepseek-reasoner",
    },
    "kimi-k2.5": {
        "model": "litellm_proxy/moonshot/kimi-k2.5",
        "temperature": 1.0,
        "top_p": 0.95,
    },
}


def load_compliance_tests(patterns: list[str] | None = None) -> list[tuple[str, type]]:
    """Load all API compliance test classes from test files.

    Args:
        patterns: Optional list of pattern names to filter by

    Returns:
        List of (file_path, test_class) tuples
    """
    test_dir = Path(__file__).parent.parent / "tests"
    test_files = sorted(test_dir.glob("a[0-9][0-9]_*.py"))

    tests = []
    for test_file in test_files:
        try:
            spec = importlib.util.spec_from_file_location("test_module", test_file)
            if spec is None or spec.loader is None:
                continue

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Find the test class
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BaseAPIComplianceTest)
                    and attr is not BaseAPIComplianceTest
                ):
                    # Check pattern filter
                    test_instance = attr()
                    if patterns is None or test_instance.pattern_name in patterns:
                        tests.append((str(test_file), attr))
                    break

        except Exception as e:
            logger.warning(f"Failed to load test from {test_file}: {e}")

    return tests


def run_single_test(
    test_class: type[BaseAPIComplianceTest],
    llm_config: dict[str, Any],
    model_id: str,
) -> ComplianceTestResult:
    """Run a single compliance test against a single model.

    Args:
        test_class: The test class to instantiate and run
        llm_config: LLM configuration dict
        model_id: Model identifier for logging

    Returns:
        ComplianceTestResult
    """
    test = test_class()

    try:
        llm = create_test_llm(llm_config)
        result = test.run_test(llm)
        return result
    except Exception as e:
        return ComplianceTestResult(
            pattern_name=test.pattern_name,
            model=llm_config.get("model", "unknown"),
            provider="unknown",
            response_type=APIResponse.CONNECTION_ERROR,
            error_message=f"Failed to create LLM: {e}",
            error_type=type(e).__name__,
        )


def run_compliance_tests(
    patterns: list[str] | None = None,
    model_ids: list[str] | None = None,
) -> ComplianceReport:
    """Run compliance tests across multiple models and patterns.

    Args:
        patterns: List of pattern names to test (None = all)
        model_ids: List of model IDs to test (None = all defaults)

    Returns:
        ComplianceReport with all results
    """
    # Load tests
    tests = load_compliance_tests(patterns)
    if not tests:
        logger.error("No compliance tests found!")
        sys.exit(1)

    logger.info(f"Loaded {len(tests)} compliance test(s)")

    # Determine models to test
    if model_ids:
        models = {
            mid: DEFAULT_MODELS[mid] for mid in model_ids if mid in DEFAULT_MODELS
        }
        if not models:
            logger.error(
                f"No valid models found. Available: {list(DEFAULT_MODELS.keys())}"
            )
            sys.exit(1)
    else:
        models = DEFAULT_MODELS

    logger.info(f"Testing against {len(models)} model(s): {list(models.keys())}")

    # Generate run ID
    run_id = f"compliance_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # Run all tests
    pattern_results: dict[str, PatternResults] = {}

    for file_path, test_class in tests:
        test_instance = test_class()
        pattern_name = test_instance.pattern_name

        if pattern_name not in pattern_results:
            pattern_results[pattern_name] = PatternResults(
                pattern_name=pattern_name,
                pattern_description=test_instance.pattern_description,
            )

        for model_id, llm_config in models.items():
            logger.info(f"Testing pattern '{pattern_name}' against {model_id}...")

            result = run_single_test(test_class, llm_config, model_id)
            pattern_results[pattern_name].add_result(result)

            # Log result
            status = (
                "✓ ACCEPTED"
                if result.response_type == APIResponse.ACCEPTED
                else "✗ REJECTED"
            )
            if result.response_type not in (APIResponse.ACCEPTED, APIResponse.REJECTED):
                status = f"⚠ {result.response_type.value.upper()}"

            logger.info(f"  {model_id}: {status}")
            if result.error_message:
                # Truncate long error messages
                msg = (
                    result.error_message[:200] + "..."
                    if len(result.error_message) > 200
                    else result.error_message
                )
                logger.info(f"    Error: {msg}")

    # Build report
    report = ComplianceReport(
        test_run_id=run_id,
        timestamp=datetime.now().isoformat(),
        patterns_tested=len(pattern_results),
        models_tested=list(models.keys()),
        results=list(pattern_results.values()),
    )

    return report


def save_report(report: ComplianceReport, output_dir: str) -> str:
    """Save report to output directory.

    Args:
        report: ComplianceReport to save
        output_dir: Directory to save to

    Returns:
        Path to saved report
    """
    os.makedirs(output_dir, exist_ok=True)

    # Save JSON report
    json_path = os.path.join(output_dir, "compliance_report.json")
    with open(json_path, "w") as f:
        f.write(report.model_dump_json(indent=2))

    # Generate and save markdown report
    md_path = os.path.join(output_dir, "compliance_report.md")
    with open(md_path, "w") as f:
        f.write(generate_markdown_report(report))

    return json_path


def generate_markdown_report(report: ComplianceReport) -> str:
    """Generate a human-readable markdown report.

    Args:
        report: ComplianceReport to format

    Returns:
        Markdown string
    """
    lines = [
        "# API Compliance Test Report",
        "",
        f"**Run ID:** {report.test_run_id}",
        f"**Timestamp:** {report.timestamp}",
        f"**Patterns Tested:** {report.patterns_tested}",
        f"**Models Tested:** {', '.join(report.models_tested)}",
        "",
        "## Summary",
        "",
        f"- Total tests: {report.total_tests}",
        f"- Rejected (expected): {report.total_rejected}",
        f"- Accepted (unexpected): {report.total_accepted}",
        "",
        "## Results by Pattern",
        "",
    ]

    for pattern in report.results:
        lines.append(f"### {pattern.pattern_name}")
        lines.append("")
        lines.append(f"_{pattern.pattern_description.strip()}_")
        lines.append("")
        lines.append("| Model | Response | Error Type | Details |")
        lines.append("|-------|----------|------------|---------|")

        for result in pattern.results:
            response = result.response_type.value
            error_type = result.error_type or "-"
            # Truncate error message for table
            details = (result.error_message or "-")[:80]
            if len(result.error_message or "") > 80:
                details += "..."
            # Escape pipe characters in details
            details = details.replace("|", "\\|")
            lines.append(f"| {result.model} | {response} | {error_type} | {details} |")

        lines.append("")

    # Add detailed error messages section
    lines.append("## Detailed Error Messages")
    lines.append("")

    for pattern in report.results:
        lines.append(f"### {pattern.pattern_name}")
        lines.append("")

        for result in pattern.results:
            if result.error_message:
                lines.append(f"**{result.model}** ({result.response_type.value}):")
                lines.append("```")
                lines.append(result.error_message)
                lines.append("```")
                lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Run API compliance tests against LLM providers"
    )
    parser.add_argument(
        "--patterns",
        type=str,
        default=None,
        help="Comma-separated list of pattern names to test (default: all)",
    )
    available_models = ", ".join(DEFAULT_MODELS.keys())
    parser.add_argument(
        "--models",
        type=str,
        default=None,
        help=f"Comma-separated list of model IDs. Available: {available_models}",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="tests/integration/api_compliance/outputs",
        help="Output directory for reports",
    )
    parser.add_argument(
        "--list-patterns",
        action="store_true",
        help="List available patterns and exit",
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="List available models and exit",
    )

    args = parser.parse_args()

    if args.list_models:
        print("Available models:")
        for model_id, config in DEFAULT_MODELS.items():
            print(f"  {model_id}: {config.get('model', 'unknown')}")
        return

    if args.list_patterns:
        tests = load_compliance_tests()
        print("Available patterns:")
        for _, test_class in tests:
            test = test_class()
            first_line = test.pattern_description.strip().split(chr(10))[0]
            print(f"  {test.pattern_name}: {first_line}")
        return

    # Parse filters
    patterns = args.patterns.split(",") if args.patterns else None
    model_ids = args.models.split(",") if args.models else None

    # Run tests
    logger.info("=" * 60)
    logger.info("API COMPLIANCE TEST RUNNER")
    logger.info("=" * 60)

    start_time = time.time()
    report = run_compliance_tests(patterns=patterns, model_ids=model_ids)
    elapsed = time.time() - start_time

    # Save report
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = os.path.join(args.output_dir, f"run_{timestamp}")
    save_report(report, output_dir)

    # Print summary
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Total tests: {report.total_tests}")
    logger.info(f"Rejected (expected): {report.total_rejected}")
    logger.info(f"Accepted (unexpected): {report.total_accepted}")
    logger.info(f"Elapsed time: {elapsed:.1f}s")
    logger.info(f"Report saved to: {output_dir}")


if __name__ == "__main__":
    main()
