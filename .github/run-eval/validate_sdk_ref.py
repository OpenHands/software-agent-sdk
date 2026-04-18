#!/usr/bin/env python3
"""
Validate SDK reference for semantic versioning.

This script validates that the SDK reference is a semantic version (e.g., v1.0.0, 1.0.0)
unless the allow_unreleased_branches flag is set.

Environment variables:
- SDK_REF: The SDK reference to validate
- ALLOW_UNRELEASED_BRANCHES: If 'true', bypass semantic version validation

Exit codes:
- 0: Validation passed
- 1: Validation failed
"""

import os
import re
import subprocess
import sys


# Semantic version pattern: optional 'v' prefix, followed by MAJOR.MINOR.PATCH
# Optionally allows pre-release (-alpha.1, -beta.2, -rc.1) and build metadata
SEMVER_PATTERN = re.compile(
    r"^v?"  # Optional 'v' prefix
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"  # MAJOR.MINOR.PATCH
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"  # Pre-release
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"  # More pre-release
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"  # Build metadata
)
COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")
BRANCH_EXAMPLES = "'main', 'feature/foo', or 'release/1.2.3'"


def is_semantic_version(ref: str) -> bool:
    """Check if the given reference is a valid semantic version."""
    return bool(SEMVER_PATTERN.match(ref))


def is_commit_sha(ref: str) -> bool:
    """Check if the given reference is a git commit SHA."""
    return bool(COMMIT_SHA_PATTERN.fullmatch(ref))


def is_valid_branch_name(ref: str) -> bool:
    """Check if the given reference is a valid git branch name."""
    return (
        subprocess.run(
            ["git", "check-ref-format", "--branch", ref],
            check=False,
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )


def validate_branch_name(branch_name: str, input_name: str) -> tuple[bool, str]:
    """Validate a workflow branch input against git branch naming rules."""
    if is_valid_branch_name(branch_name):
        return True, f"Valid {input_name}: {branch_name}"

    return False, (
        f"{input_name} '{branch_name}' is not a valid git branch name. "
        f"Common GitHub/GitLab/Bitbucket branch names look like {BRANCH_EXAMPLES}."
    )


def validate_sdk_ref(sdk_ref: str, allow_unreleased: bool) -> tuple[bool, str]:
    """Validate the SDK reference."""
    if is_semantic_version(sdk_ref):
        return True, f"Valid semantic version: {sdk_ref}"

    if allow_unreleased and (is_commit_sha(sdk_ref) or is_valid_branch_name(sdk_ref)):
        return True, f"Valid unreleased git ref: {sdk_ref}"

    if allow_unreleased:
        return False, (
            f"SDK reference '{sdk_ref}' is not a valid semantic version, commit SHA, "
            "or git branch name. Common GitHub/GitLab/Bitbucket branch names look "
            f"like {BRANCH_EXAMPLES}."
        )

    return False, (
        f"SDK reference '{sdk_ref}' is not a valid semantic version. "
        "Expected format: v1.0.0 or 1.0.0 (with optional pre-release like -alpha.1). "
        "To use unreleased branches, check 'Allow unreleased branches'."
    )


def main() -> None:
    sdk_ref = os.environ.get("SDK_REF", "")
    allow_unreleased_str = os.environ.get("ALLOW_UNRELEASED_BRANCHES", "false")
    eval_branch = os.environ.get("EVAL_BRANCH")
    benchmarks_branch = os.environ.get("BENCHMARKS_BRANCH")

    if not sdk_ref:
        print("ERROR: SDK_REF environment variable is not set", file=sys.stderr)
        sys.exit(1)

    allow_unreleased = allow_unreleased_str.lower() == "true"

    validations = [
        validate_sdk_ref(sdk_ref, allow_unreleased),
    ]
    if eval_branch:
        validations.append(validate_branch_name(eval_branch, "EVAL_BRANCH"))
    if benchmarks_branch:
        validations.append(validate_branch_name(benchmarks_branch, "BENCHMARKS_BRANCH"))

    for is_valid, message in validations:
        stream = sys.stdout if is_valid else sys.stderr
        print(("✓" if is_valid else "✗") + f" {message}", file=stream)
        if not is_valid:
            sys.exit(1)


if __name__ == "__main__":
    main()
