#!/usr/bin/env python3
"""
Local Test Script for SRE Error Debugger

This script allows you to test the error debugger locally. It will run a small
subset of tests to capture failures, then analyze them.

Usage:
    export LLM_API_KEY="your-api-key"
    python test_local.py

Optional environment variables you can override:
    TEST_PATH: Path to tests (default: tests/sdk/ for speed)
    TEST_OUTPUT_FILE: Use pre-generated test output instead of running tests
    LLM_MODEL: Model to use (default: openhands/claude-sonnet-4-5-20250929)
"""

import os
import sys
from pathlib import Path


def main():
    """Run the error debugger locally with sensible defaults."""

    # Check for required API key
    if not os.getenv("LLM_API_KEY"):
        print("Error: LLM_API_KEY environment variable must be set")
        print("Export it before running this script:")
        print("  export LLM_API_KEY='your-api-key'")
        sys.exit(1)

    # Set default values if not already set
    if not os.getenv("TEST_PATH"):
        # Use a small subset of tests for speed
        os.environ["TEST_PATH"] = "tests/sdk/"
        print("Using default TEST_PATH: tests/sdk/")

    if not os.getenv("LLM_MODEL"):
        os.environ["LLM_MODEL"] = "openhands/claude-sonnet-4-5-20250929"

    # Don't create PR for local testing
    if not os.getenv("CREATE_PR"):
        os.environ["CREATE_PR"] = "false"

    print("\n" + "=" * 60)
    print("Starting SRE Error Debugger Test")
    print("=" * 60)
    print(f"Test path: {os.environ['TEST_PATH']}")
    print(f"Model: {os.environ['LLM_MODEL']}")
    print(f"Working directory: {os.getcwd()}")
    print("=" * 60 + "\n")

    print("NOTE: This will run tests first to capture failures.")
    print("If all tests pass, the debugger will exit gracefully.\n")

    # Import and run the main agent script
    try:
        import agent_script

        agent_script.main()

        print("\n" + "=" * 60)
        print("Test completed!")
        print("=" * 60)

        # Check if analysis was generated
        if Path("ERROR_ANALYSIS.md").exists():
            print("\n✅ ERROR_ANALYSIS.md generated successfully")
            print("Review the file for debugging insights.")
        else:
            print("\n✅ No failures detected or analysis not generated")

    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nTest failed with error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
