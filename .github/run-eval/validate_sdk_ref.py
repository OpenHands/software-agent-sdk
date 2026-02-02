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


def is_semantic_version(ref: str) -> bool:
    """Check if the given reference is a valid semantic version.

    Args:
        ref: The reference string to validate

    Returns:
        True if the reference is a valid semantic version, False otherwise
    """
    return bool(SEMVER_PATTERN.match(ref))


def validate_sdk_ref(sdk_ref: str, allow_unreleased: bool) -> tuple[bool, str]:
    """Validate the SDK reference.

    Args:
        sdk_ref: The SDK reference to validate
        allow_unreleased: If True, bypass semantic version validation

    Returns:
        Tuple of (is_valid, message)
    """
    if allow_unreleased:
        return True, f"Allowing unreleased branch: {sdk_ref}"

    if is_semantic_version(sdk_ref):
        return True, f"Valid semantic version: {sdk_ref}"

    return False, (
        f"SDK reference '{sdk_ref}' is not a valid semantic version. "
        "Expected format: v1.0.0 or 1.0.0 (with optional pre-release like -alpha.1). "
        "To use unreleased branches, check 'Allow unreleased branches'."
    )


def main() -> None:
    sdk_ref = os.environ.get("SDK_REF", "")
    allow_unreleased_str = os.environ.get("ALLOW_UNRELEASED_BRANCHES", "false")

    if not sdk_ref:
        print("ERROR: SDK_REF environment variable is not set", file=sys.stderr)
        sys.exit(1)

    allow_unreleased = allow_unreleased_str.lower() == "true"

    is_valid, message = validate_sdk_ref(sdk_ref, allow_unreleased)

    if is_valid:
        print(f"✓ {message}")
        sys.exit(0)
    else:
        print(f"✗ {message}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
