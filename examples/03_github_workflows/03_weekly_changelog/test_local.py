#!/usr/bin/env python3
"""
Local Test Script for Changelog Generation

This script allows you to test the changelog generator locally without setting up
GitHub Actions. It sets default environment variables and runs the agent script.

Usage:
    export LLM_API_KEY="your-api-key"
    python test_local.py

Optional environment variables you can override:
    START_REF: Start reference - date (YYYY-MM-DD), commit SHA, or tag.
        Default: 7 days ago
    END_REF: End reference - date (YYYY-MM-DD), commit SHA, or tag.
        Default: today
    START_DATE: Alias for START_REF (backward compatibility)
    END_DATE: Alias for END_REF (backward compatibility)
    LLM_MODEL: Model to use, default: openhands/claude-sonnet-4-5-20250929
    CREATE_PR: Whether to create PR, default: false (recommended for local testing)
"""

import os
import sys
from datetime import datetime, timedelta


def main():
    """Run the changelog generator locally with sensible defaults."""

    # Check for required API key
    if not os.getenv("LLM_API_KEY"):
        print("Error: LLM_API_KEY environment variable must be set")
        print("Export it before running this script:")
        print("  export LLM_API_KEY='your-api-key'")
        sys.exit(1)

    # Set default values if not already set
    # Check both new (START_REF) and old (START_DATE) env vars
    if not os.getenv("START_REF") and not os.getenv("START_DATE"):
        # Default to 7 days ago
        default_start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        os.environ["START_REF"] = default_start
        print(f"Using default START_REF: {default_start}")
    else:
        start = os.getenv("START_REF") or os.getenv("START_DATE")
        print(f"Using START_REF: {start}")

    if not os.getenv("END_REF") and not os.getenv("END_DATE"):
        # Default to today
        default_end = datetime.now().strftime("%Y-%m-%d")
        os.environ["END_REF"] = default_end
        print(f"Using default END_REF: {default_end}")
    else:
        end = os.getenv("END_REF") or os.getenv("END_DATE")
        print(f"Using END_REF: {end}")

    if not os.getenv("LLM_MODEL"):
        os.environ["LLM_MODEL"] = "openhands/claude-sonnet-4-5-20250929"

    # Disable PR creation for local testing by default
    if not os.getenv("CREATE_PR"):
        os.environ["CREATE_PR"] = "false"
        print("CREATE_PR=false (no PR will be created, safe for local testing)")

    # Get the actual refs being used
    start_ref = os.getenv("START_REF") or os.getenv("START_DATE")
    end_ref = os.getenv("END_REF") or os.getenv("END_DATE")

    print("\n" + "=" * 60)
    print("Starting local changelog generation test")
    print("=" * 60)
    print(f"Range: {start_ref} to {end_ref}")
    print(f"Model: {os.environ['LLM_MODEL']}")
    print(f"Working directory: {os.getcwd()}")
    print("=" * 60 + "\n")

    # Import and run the main agent script
    try:
        import agent_script

        agent_script.main()

        print("\n" + "=" * 60)
        print("Test completed successfully!")
        print("=" * 60)
        print("\nCheck the CHANGELOG.md file in your current directory")

    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nTest failed with error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
