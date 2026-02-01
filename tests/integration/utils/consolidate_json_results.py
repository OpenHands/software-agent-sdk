#!/usr/bin/env python3
"""
Consolidate JSON test results from multiple models into a single structured file.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from tests.integration.schemas import (
    ConsolidatedResults,
    ModelTestResults,
)


def find_json_results(results_dir: str) -> list[Path]:
    """Find all JSON result files in the results directory."""
    results_path = Path(results_dir)
    if not results_path.exists():
        raise FileNotFoundError(f"Results directory not found: {results_dir}")

    # Look for both patterns: */results.json and *_results.json
    json_files = list(results_path.glob("*/results.json")) + list(
        results_path.glob("*_results.json")
    )
    print(f"Found {len(json_files)} JSON result files")

    for json_file in json_files:
        print(f"  - {json_file}")

    return json_files


def load_and_validate_results(
    json_files: list[Path], artifacts_dir: str | None = None
) -> list[ModelTestResults]:
    """Load and validate JSON result files."""
    model_results = []

    for json_file in json_files:
        try:
            print(f"Loading {json_file}...")
            with open(json_file) as f:
                data = json.load(f)

            # Validate using Pydantic schema
            model_result = ModelTestResults.model_validate(data)

            # Add artifact URL if artifacts directory is provided
            if artifacts_dir:
                artifact_url = find_artifact_url(model_result.run_suffix, artifacts_dir)
                if artifact_url:
                    model_result.artifact_url = artifact_url

            model_results.append(model_result)
            model_name = model_result.model_name
            total_tests = model_result.total_tests
            print(f"  ✓ Loaded {model_name} with {total_tests} tests")

        except Exception as e:
            print(f"  ✗ Error loading {json_file}: {e}")
            raise

    return model_results


def extract_matrix_run_suffix(full_run_suffix: str) -> str | None:
    """
    Extract the matrix run-suffix from the full run_suffix.

    The full run_suffix format is:
    {model_name}_{commit_hash}_{matrix_run_suffix}_N{count}_{timestamp}
    We need to extract the matrix_run_suffix part.

    Examples:
    - litellm_proxy_anthropic_claude_sonnet_4_5_20250929_0dd44e1_sonnet_run_N7_20251006_183106
      -> sonnet_run
    - litellm_proxy_deepseek_deepseek_chat_0dd44e1_deepseek_run_N7_20251006_183104
      -> deepseek_run
    - litellm_proxy_openai_gpt_5_mini_0dd44e1_gpt5_mini_run_N7_20251006_183117
      -> gpt5_mini_run
    """  # noqa: E501
    import re

    # Pattern to match the matrix run suffix
    # Look for pattern: _{7_hex_chars}_{matrix_run_suffix}_N{number}_
    # The commit hash is always 7 hex characters
    pattern = r"_[a-f0-9]{7}_([^_]+(?:_[^_]+)*_run)_N\d+_"
    match = re.search(pattern, full_run_suffix)

    if match:
        return match.group(1)

    # Fallback: if pattern doesn't match, return None
    return None


def find_artifact_url(run_suffix: str, artifacts_dir: str) -> str | None:
    """Find the artifact URL for a given run suffix."""
    artifacts_path = Path(artifacts_dir)
    if not artifacts_path.exists():
        return None

    # Extract the matrix run-suffix from the full run_suffix
    matrix_run_suffix = extract_matrix_run_suffix(run_suffix)
    if not matrix_run_suffix:
        return None

    # Look for artifact directories that match the matrix run suffix
    # Artifact naming pattern: integration-test-outputs-{matrix-run-suffix}-{run-id}-{run-attempt}  # noqa: E501
    expected_prefix = f"integration-test-outputs-{matrix_run_suffix}-"

    for artifact_dir in artifacts_path.iterdir():
        if artifact_dir.is_dir() and artifact_dir.name.startswith(expected_prefix):
            # Generate GitHub Actions URL using environment variables
            server_url = os.getenv("GITHUB_SERVER_URL", "https://github.com")
            repository = os.getenv("GITHUB_REPOSITORY", "")
            run_id = os.getenv("GITHUB_RUN_ID", "")

            if repository and run_id:
                # Create a URL that points to the GitHub Actions run page
                # Users can download the specific artifact from there
                return f"{server_url}/{repository}/actions/runs/{run_id}"
            else:
                # Fallback: if environment variables not available, return None
                # This will prevent showing broken links
                return None

    return None


def consolidate_results(model_results: list[ModelTestResults]) -> ConsolidatedResults:
    """Consolidate individual model results into a single structure."""
    print(f"\nConsolidating {len(model_results)} model results...")

    consolidated = ConsolidatedResults.from_model_results(model_results)

    print(f"Overall success rate: {consolidated.overall_success_rate:.2%}")
    print(f"Total cost across all models: ${consolidated.total_cost_all_models:.4f}")

    # Print per-model token usage summary
    # Note: We don't aggregate tokens across models because different models
    # use different tokenizers, making cross-model token sums meaningless.
    for model_result in model_results:
        if model_result.total_token_usage is not None:
            token_usage = model_result.total_token_usage
            total_tokens = token_usage.prompt_tokens + token_usage.completion_tokens
            print(f"Token usage for {model_result.model_name}: {total_tokens:,}")

    return consolidated


def save_consolidated_results(
    consolidated: ConsolidatedResults, output_file: str
) -> None:
    """Save consolidated results to JSON file."""
    print(f"\nSaving consolidated results to {output_file}...")

    # Only create directory if output_file has a directory component
    output_dir = os.path.dirname(output_file)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(output_file, "w") as f:
        f.write(consolidated.model_dump_json(indent=2))

    print(f"✓ Consolidated results saved to {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description="Consolidate JSON test results from multiple models"
    )
    parser.add_argument(
        "--results-dir",
        required=True,
        help="Directory containing model result subdirectories",
    )
    parser.add_argument(
        "--output-file",
        required=True,
        help="Output file for consolidated results",
    )
    parser.add_argument(
        "--artifacts-dir",
        help="Directory containing downloaded artifacts for URL generation",
    )

    args = parser.parse_args()

    try:
        # Find all JSON result files
        json_files = find_json_results(args.results_dir)

        if not json_files:
            print("No JSON result files found!")
            return 1

        # Load and validate results
        model_results = load_and_validate_results(json_files, args.artifacts_dir)

        # Consolidate results
        consolidated = consolidate_results(model_results)

        # Save consolidated results
        save_consolidated_results(consolidated, args.output_file)

        print("\n✓ Consolidation completed successfully!")
        return 0

    except Exception as e:
        print(f"\n✗ Error during consolidation: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
