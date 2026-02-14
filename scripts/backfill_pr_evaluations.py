#!/usr/bin/env python3
"""
Backfill PR Review Evaluations

This script backfills pr_review_evaluation spans for PRs that were reviewed
but never had their evaluations run (e.g., due to workflow failures).

It:
1. Fetches all pr-review-trace artifacts from GitHub Actions
2. For each trace, checks if the PR was closed/merged
3. Runs the evaluation to add the span to the original trace

Usage:
    GITHUB_TOKEN=xxx LMNR_PROJECT_API_KEY=xxx python backfill_pr_evaluations.py

Environment Variables:
    GITHUB_TOKEN: GitHub token for API access (required)
    LMNR_PROJECT_API_KEY: Laminar project API key (required)
    DRY_RUN: If set to "true", only list what would be done (optional)
    MAX_PRs: Maximum number of PRs to process (optional, default: 50)
"""

import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def get_env(name: str, required: bool = True) -> str | None:
    """Get environment variable."""
    value = os.getenv(name)
    if required and not value:
        print(f"Error: {name} environment variable is required")
        sys.exit(1)
    return value


def run_gh_api(endpoint: str) -> dict | list:
    """Run GitHub API request via gh CLI."""
    result = subprocess.run(
        ["gh", "api", endpoint],
        capture_output=True,
        text=True,
        env={**os.environ, "GH_TOKEN": get_env("GITHUB_TOKEN")},
    )
    if result.returncode != 0:
        print(f"Error calling GitHub API: {result.stderr}")
        return {}
    return json.loads(result.stdout)


def download_artifact(artifact_id: int, path: Path) -> bool:
    """Download and extract an artifact."""
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/OpenHands/software-agent-sdk/actions/artifacts/{artifact_id}/zip",
            ],
            capture_output=True,
            env={**os.environ, "GH_TOKEN": get_env("GITHUB_TOKEN")},
        )
        if result.returncode != 0:
            return False

        # Write and extract zip
        zip_path = path / "artifact.zip"
        zip_path.write_bytes(result.stdout)

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(path)

        zip_path.unlink()
        return True
    except Exception as e:
        print(f"Error downloading artifact {artifact_id}: {e}")
        return False


def get_pr_info(pr_number: str) -> dict:
    """Get PR information."""
    return run_gh_api(f"repos/OpenHands/software-agent-sdk/pulls/{pr_number}")


def get_trace_artifacts(max_pages: int = 20) -> list[dict]:
    """Get all pr-review-trace artifacts."""
    artifacts = []
    seen_prs = set()

    for page in range(1, max_pages + 1):
        data = run_gh_api(
            f"repos/OpenHands/software-agent-sdk/actions/artifacts?per_page=100&page={page}"
        )
        if not data or not data.get("artifacts"):
            break

        for artifact in data["artifacts"]:
            name = artifact.get("name", "")
            if name.startswith("pr-review-trace-"):
                pr_num = name.replace("pr-review-trace-", "")
                # Only keep most recent artifact per PR
                if pr_num not in seen_prs:
                    seen_prs.add(pr_num)
                    artifacts.append(
                        {
                            "pr_number": pr_num,
                            "artifact_id": artifact["id"],
                            "created_at": artifact["created_at"],
                        }
                    )

    return artifacts


def get_evaluation_artifacts() -> set[str]:
    """Get PR numbers that already have evaluations."""
    evaluated = set()
    for page in range(1, 10):
        data = run_gh_api(
            f"repos/OpenHands/software-agent-sdk/actions/artifacts?per_page=100&page={page}"
        )
        if not data or not data.get("artifacts"):
            break

        for artifact in data["artifacts"]:
            name = artifact.get("name", "")
            if name.startswith("pr-review-evaluation-"):
                pr_num = name.replace("pr-review-evaluation-", "")
                evaluated.add(pr_num)

    return evaluated


def run_evaluation(pr_number: str, trace_info_path: Path, pr_merged: bool) -> bool:
    """Run the evaluation script."""
    try:
        env = {
            **os.environ,
            "PR_NUMBER": pr_number,
            "REPO_NAME": "OpenHands/software-agent-sdk",
            "PR_MERGED": str(pr_merged).lower(),
            "GITHUB_TOKEN": get_env("GITHUB_TOKEN"),
            "LMNR_PROJECT_API_KEY": get_env("LMNR_PROJECT_API_KEY"),
        }

        # Run evaluation script
        script_path = (
            Path(__file__).parent.parent
            / "examples/03_github_workflows/02_pr_review/evaluate_review.py"
        )

        result = subprocess.run(
            ["python", str(script_path)],
            cwd=trace_info_path.parent,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            print(f"  Evaluation failed: {result.stderr[:500]}")
            return False

        print(f"  Evaluation output: {result.stdout[-500:]}")
        return True
    except Exception as e:
        print(f"  Error running evaluation: {e}")
        return False


def main():
    dry_run = get_env("DRY_RUN", required=False) == "true"
    max_prs = int(get_env("MAX_PRS", required=False) or "50")

    print("Fetching trace artifacts...")
    artifacts = get_trace_artifacts()
    print(f"Found {len(artifacts)} trace artifacts")

    print("Fetching already-evaluated PRs...")
    evaluated = get_evaluation_artifacts()
    print(f"Found {len(evaluated)} already-evaluated PRs")

    # Filter to PRs that need evaluation
    to_evaluate = []
    for artifact in artifacts:
        pr_num = artifact["pr_number"]
        if pr_num in evaluated:
            continue

        # Check PR state
        pr_info = get_pr_info(pr_num)
        if not pr_info:
            print(f"  PR #{pr_num}: Not found")
            continue

        state = pr_info.get("state", "")
        merged = pr_info.get("merged", False)

        if state != "closed":
            print(f"  PR #{pr_num}: Still open, skipping")
            continue

        to_evaluate.append(
            {
                **artifact,
                "merged": merged,
                "title": pr_info.get("title", ""),
            }
        )

    print(f"\nPRs to evaluate: {len(to_evaluate)}")
    for item in to_evaluate[:max_prs]:
        status = "merged" if item["merged"] else "closed"
        print(f"  #{item['pr_number']}: {item['title'][:50]}... ({status})")

    if dry_run:
        print("\nDry run mode - not running evaluations")
        return

    # Run evaluations
    print(f"\nRunning evaluations for up to {max_prs} PRs...")
    success = 0
    failed = 0

    for item in to_evaluate[:max_prs]:
        pr_num = item["pr_number"]
        print(f"\nProcessing PR #{pr_num}...")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Download artifact
            if not download_artifact(item["artifact_id"], tmppath):
                print(f"  Failed to download artifact")
                failed += 1
                continue

            trace_file = tmppath / "laminar_trace_info.json"
            if not trace_file.exists():
                print(f"  Trace file not found in artifact")
                failed += 1
                continue

            # Run evaluation
            if run_evaluation(pr_num, trace_file, item["merged"]):
                success += 1
            else:
                failed += 1

    print(f"\n=== Summary ===")
    print(f"Successful: {success}")
    print(f"Failed: {failed}")


if __name__ == "__main__":
    main()
