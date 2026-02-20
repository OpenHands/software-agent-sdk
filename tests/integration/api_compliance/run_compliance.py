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
    "qwen3-coder": {
        "model": "litellm_proxy/qwen/qwen3-coder",
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


# Base URL for linking to test files
GITHUB_BASE_URL = (
    "https://github.com/OpenHands/software-agent-sdk/blob/main/tests/integration/tests"
)

# Map pattern names to test file names
PATTERN_TO_FILE = {
    "unmatched_tool_use": "a01_unmatched_tool_use.py",
    "unmatched_tool_result": "a02_unmatched_tool_result.py",
    "interleaved_user_message": "a03_interleaved_user_msg.py",
    "interleaved_assistant_message": "a04_interleaved_asst_msg.py",
    "duplicate_tool_call_id": "a05_duplicate_tool_call_id.py",
    "wrong_tool_call_id": "a06_wrong_tool_call_id.py",
    "parallel_missing_result": "a07_parallel_missing_result.py",
    "parallel_wrong_order": "a08_parallel_wrong_order.py",
}

# Brief descriptions for each pattern (one-line summaries)
PATTERN_SUMMARIES = {
    "unmatched_tool_use": "tool_use without following tool_result",
    "unmatched_tool_result": "tool_result referencing non-existent tool_use ID",
    "interleaved_user_message": "User message between tool_use and tool_result",
    "interleaved_assistant_message": "Assistant message between tool_use/tool_result",
    "duplicate_tool_call_id": "Same tool_call ID used in multiple tool_use blocks",
    "wrong_tool_call_id": "tool_result with mismatched tool_call_id",
    "parallel_missing_result": "Parallel tool calls with one result missing",
    "parallel_wrong_order": "Parallel tool call results in wrong order",
}


def generate_markdown_report(report: ComplianceReport) -> str:
    """Generate a compact, human-readable markdown report.

    Args:
        report: ComplianceReport to format

    Returns:
        Markdown string
    """
    lines = [
        "# API Compliance Test Report",
        "",
        f"**Run:** `{report.test_run_id}` | "
        f"**Time:** {report.timestamp} | "
        f"**Duration:** {report.elapsed_time:.1f}s",
        "",
    ]

    # Build results matrix: pattern -> model_id -> result
    # Note: result.model contains full model path, models_tested has short IDs
    models = report.models_tested
    results_map: dict[str, dict[str, str]] = {}

    for pattern in report.results:
        results_map[pattern.pattern_name] = {}
        for result in pattern.results:
            # Map response type to colored emoji for quick scanning
            result_symbol = "🟡"  # Yellow = other/error
            if result.response_type == APIResponse.ACCEPTED:
                result_symbol = "🟢"  # Green = accepted
            elif result.response_type == APIResponse.REJECTED:
                result_symbol = "🔴"  # Red = rejected

            # Find matching model ID from full model path
            # e.g., "litellm_proxy/claude-sonnet-4-5-20250929" -> "claude-sonnet-4-5"
            for model_id in models:
                if model_id in result.model:
                    results_map[pattern.pattern_name][model_id] = result_symbol
                    break

    # Generate results table
    lines.append("## Results Matrix")
    lines.append("")
    lines.append("🟢 accepted  🔴 rejected  🟡 error")
    lines.append("")

    # Table header
    header = "| Pattern | " + " | ".join(models) + " |"
    separator = "|:--------|" + "|".join([":---:" for _ in models]) + "|"
    lines.append(header)
    lines.append(separator)

    # Table rows
    for pattern_name in results_map:
        summary = PATTERN_SUMMARIES.get(pattern_name, "")
        file_name = PATTERN_TO_FILE.get(pattern_name, "")
        if file_name:
            link = f"[`{pattern_name}`]({GITHUB_BASE_URL}/{file_name})"
        else:
            link = f"`{pattern_name}`"

        row = f"| {link}<br><sub>{summary}</sub> |"
        for model in models:
            result = results_map[pattern_name].get(model, "-")
            row += f" {result} |"
        lines.append(row)

    lines.append("")

    # Summary stats
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Total tests:** {report.total_tests}")
    lines.append(
        f"- **Rejected (expected for malformed input):** {report.total_rejected}"
    )
    lines.append(f"- **Accepted (lenient API behavior):** {report.total_accepted}")
    lines.append("")

    # Note about detailed responses with link to workflow run
    lines.append("---")
    lines.append("")
    # Link to workflow run page (artifacts are downloadable from there)
    github_run_id = os.environ.get("GITHUB_RUN_ID")
    if github_run_id:
        run_url = (
            "https://github.com/OpenHands/software-agent-sdk/actions/runs/"
            f"{github_run_id}"
        )
        lines.append(
            f"*Full API responses available in [workflow artifacts]({run_url})*"
        )
    else:
        lines.append("*Full API responses available in `compliance_report.json`*")

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
    report.elapsed_time = elapsed

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
