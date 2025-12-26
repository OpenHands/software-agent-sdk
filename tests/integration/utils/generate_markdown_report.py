#!/usr/bin/env python3
"""
Generate markdown report for PR comments from consolidated JSON results.
"""

import argparse
import json
import sys

from tests.integration.schemas import (
    ConsolidatedResults,
    ModelTestResults,
    TokenUsageData,
)
from tests.integration.utils.format_costs import format_cost


def format_token_usage(token_usage: TokenUsageData | None) -> str:
    """Format token usage for display."""
    if token_usage is None:
        return "N/A"

    parts = []
    if token_usage.prompt_tokens > 0:
        parts.append(f"prompt: {token_usage.prompt_tokens:,}")
    if token_usage.completion_tokens > 0:
        parts.append(f"completion: {token_usage.completion_tokens:,}")
    if token_usage.cache_read_tokens > 0:
        parts.append(f"cache_read: {token_usage.cache_read_tokens:,}")
    if token_usage.cache_write_tokens > 0:
        parts.append(f"cache_write: {token_usage.cache_write_tokens:,}")
    if token_usage.reasoning_tokens > 0:
        parts.append(f"reasoning: {token_usage.reasoning_tokens:,}")

    if not parts:
        return "0"

    return ", ".join(parts)


def format_token_usage_short(token_usage: TokenUsageData | None) -> str:
    """Format token usage in a short format for tables."""
    if token_usage is None:
        return "N/A"

    total = token_usage.prompt_tokens + token_usage.completion_tokens
    if total == 0:
        return "0"

    return f"{total:,}"


def generate_model_summary_table(model_results: list[ModelTestResults]) -> str:
    """Generate a summary table for all models."""

    table_lines = [
        (
            "| Model | Overall | Integration (Required) | Behavior (Optional) "
            "| Tests Passed | Skipped | Total | Cost | Tokens |"
        ),
        (
            "|-------|---------|------------------------|---------------------|"
            "--------------|---------|-------|------|--------|"
        ),
    ]

    for result in model_results:
        overall_success = f"{result.success_rate:.1%}"
        integration_success = (
            f"{result.integration_tests_success_rate:.1%}"
            if result.integration_tests_total > 0
            else "N/A"
        )
        behavior_success = (
            f"{result.behavior_tests_success_rate:.1%}"
            if result.behavior_tests_total > 0
            else "N/A"
        )
        non_skipped = result.total_tests - result.skipped_tests
        tests_passed = f"{result.successful_tests}/{non_skipped}"
        skipped = f"{result.skipped_tests}"
        cost = format_cost(result.total_cost)
        tokens = format_token_usage_short(result.total_token_usage)

        model_name = result.model_name
        total_tests = result.total_tests
        row = (
            f"| {model_name} | {overall_success} | {integration_success} | "
            f"{behavior_success} | {tests_passed} | {skipped} | "
            f"{total_tests} | {cost} | {tokens} |"
        )
        table_lines.append(row)

    return "\n".join(table_lines)


def generate_detailed_results(model_results: list[ModelTestResults]) -> str:
    """Generate detailed results for each model."""

    sections = []

    for result in model_results:
        non_skipped = result.total_tests - result.skipped_tests
        section_lines = [
            f"### {result.model_name}",
            "",
            f"- **Overall Success Rate**: {result.success_rate:.1%} "
            f"({result.successful_tests}/{non_skipped})",
        ]

        # Add integration tests success rate if applicable
        if result.integration_tests_total > 0:
            section_lines.append(
                f"- **Integration Tests (Required)**: "
                f"{result.integration_tests_success_rate:.1%} "
                f"({result.integration_tests_successful}/"
                f"{result.integration_tests_total})"
            )

        # Add behavior tests success rate if applicable
        if result.behavior_tests_total > 0:
            section_lines.append(
                f"- **Behavior Tests (Optional)**: "
                f"{result.behavior_tests_success_rate:.1%} "
                f"({result.behavior_tests_successful}/"
                f"{result.behavior_tests_total})"
            )

        section_lines.extend(
            [
                f"- **Total Cost**: {format_cost(result.total_cost)}",
                f"- **Token Usage**: {format_token_usage(result.total_token_usage)}",
                f"- **Run Suffix**: `{result.run_suffix}`",
            ]
        )

        if result.skipped_tests > 0:
            section_lines.append(f"- **Skipped Tests**: {result.skipped_tests}")

        section_lines.append("")

        # Add skipped tests if any
        skipped_tests = [t for t in result.test_instances if t.test_result.skipped]
        if skipped_tests:
            section_lines.extend(
                [
                    "**Skipped Tests:**",
                    "",
                ]
            )

            for test in skipped_tests:
                reason = test.test_result.reason or "No reason provided"
                section_lines.append(f"- `{test.instance_id}`: {reason}")

            section_lines.append("")

        # Add failed tests if any
        failed_tests = [
            t
            for t in result.test_instances
            if not t.test_result.success and not t.test_result.skipped
        ]
        if failed_tests:
            section_lines.extend(
                [
                    "**Failed Tests:**",
                    "",
                ]
            )

            for test in failed_tests:
                reason = test.test_result.reason or "No reason provided"
                cost = format_cost(test.cost)
                required_marker = " ⚠️ **REQUIRED**" if test.required else ""
                section_lines.append(
                    f"- `{test.instance_id}`{required_marker}: {reason} (Cost: {cost})"
                )

            section_lines.append("")

        # Add error messages if any
        error_tests = [t for t in result.test_instances if t.error_message]
        if error_tests:
            section_lines.extend(
                [
                    "**Tests with Errors:**",
                    "",
                ]
            )

            for test in error_tests:
                section_lines.append(f"- `{test.instance_id}`: {test.error_message}")

            section_lines.append("")

        sections.append("\n".join(section_lines))

    return "\n".join(sections)


def generate_markdown_report(consolidated: ConsolidatedResults) -> str:
    """Generate complete markdown report from consolidated results."""

    # Header
    report_lines = [
        "# 🧪 Integration Tests Results",
        "",
        f"**Overall Success Rate**: {consolidated.overall_success_rate:.1%}",
        f"**Total Cost**: {format_cost(consolidated.total_cost_all_models)}",
        f"**Models Tested**: {consolidated.total_models}",
        f"**Timestamp**: {consolidated.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
    ]

    # Add artifacts section if any model has artifact URLs
    artifacts_available = any(
        result.artifact_url for result in consolidated.model_results
    )
    if artifacts_available:
        report_lines.extend(
            [
                "## 📁 Detailed Logs & Artifacts",
                "",
                (
                    "Click the links below to access detailed agent/LLM logs showing "
                    "the complete reasoning process for each model. "
                    "On the GitHub Actions page, scroll down to the 'Artifacts' "
                    "section to download the logs."
                ),
                "",
            ]
        )

        for result in consolidated.model_results:
            if result.artifact_url:
                report_lines.append(
                    f"- **{result.model_name}**: "
                    f"[📥 View & Download Logs]({result.artifact_url})"
                )

        report_lines.append("")  # Add empty line after artifacts section

    # Summary table
    report_lines.extend(
        [
            "## 📊 Summary",
            "",
            generate_model_summary_table(consolidated.model_results),
            "",
        ]
    )

    # Detailed results
    report_lines.extend(
        [
            "## 📋 Detailed Results",
            "",
            generate_detailed_results(consolidated.model_results),
        ]
    )

    return "\n".join(report_lines)


def main():
    parser = argparse.ArgumentParser(
        description="Generate markdown report from consolidated JSON results"
    )
    parser.add_argument(
        "--input-file",
        required=True,
        help="Consolidated JSON results file",
    )
    parser.add_argument(
        "--output-file",
        help="Output markdown file (default: stdout)",
    )

    args = parser.parse_args()

    try:
        # Load consolidated results
        print(
            f"Loading consolidated results from {args.input_file}...", file=sys.stderr
        )

        with open(args.input_file) as f:
            data = json.load(f)

        consolidated = ConsolidatedResults.model_validate(data)
        print(
            f"✓ Loaded results for {consolidated.total_models} models", file=sys.stderr
        )

        # Generate markdown report
        print("Generating markdown report...", file=sys.stderr)
        markdown_report = generate_markdown_report(consolidated)

        # Output report
        if args.output_file:
            with open(args.output_file, "w") as f:
                f.write(markdown_report)
            print(f"✓ Report saved to {args.output_file}", file=sys.stderr)
        else:
            print(markdown_report)

        return 0

    except Exception as e:
        print(f"✗ Error generating report: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
