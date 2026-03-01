#!/usr/bin/env python3
"""
Profile script for SWE-bench style SDK conversations.

This script creates a realistic coding task to profile the SDK's core code paths:
- LLM API calls
- Tool execution (file operations)
- Conversation iteration loop

Usage:
    # Standard mode (env vars):
    LLM_CONFIG='{"model": "...", "temperature": 0.0}' \
    MAX_ITERATIONS=10 \
    python scripts/profile_conversation.py

    # CLI mode (for profiling under sudo):
    python scripts/profile_conversation.py \
        --llm-api-key="..." --llm-base-url="..." \
        --llm-config='{"model": "..."}' --max-iterations=10 \
        --site-packages="/path/to/site-packages" --sdk-dir="/path/to/sdk"
"""

import json
import os
import sys
import traceback


def setup_from_args() -> None:
    """Parse command-line arguments and set up environment for profiling."""
    # Support for command-line arguments to work around sudo stripping env vars
    for arg in sys.argv[1:]:
        if arg.startswith("--llm-api-key="):
            os.environ["LLM_API_KEY"] = arg.split("=", 1)[1]
        elif arg.startswith("--llm-base-url="):
            os.environ["LLM_BASE_URL"] = arg.split("=", 1)[1]
        elif arg.startswith("--llm-config="):
            os.environ["LLM_CONFIG"] = arg.split("=", 1)[1]
        elif arg.startswith("--max-iterations="):
            os.environ["MAX_ITERATIONS"] = arg.split("=", 1)[1]
        elif arg.startswith("--site-packages="):
            site_packages = arg.split("=", 1)[1]
            if site_packages and site_packages not in sys.path:
                sys.path.insert(0, site_packages)
        elif arg.startswith("--sdk-dir="):
            sdk_dir = arg.split("=", 1)[1]
            if sdk_dir:
                os.chdir(sdk_dir)
                if sdk_dir not in sys.path:
                    sys.path.insert(0, sdk_dir)


# Process CLI args FIRST (before imports) to set up paths
setup_from_args()

# Debug: Verify script is starting (before heavy imports)
print("[DEBUG] profile_conversation.py: Starting imports...", flush=True)
print(f"[DEBUG] sys.path[:5]: {sys.path[:5]}", flush=True)
print(f"[DEBUG] CWD: {os.getcwd()}", flush=True)

try:
    from openhands.sdk import LLM, Agent, Conversation, Tool
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.terminal import TerminalTool

    print("[DEBUG] profile_conversation.py: Imports successful", flush=True)
except Exception as e:
    print(
        f"[ERROR] Import failed: {type(e).__name__}: {e}", file=sys.stderr, flush=True
    )
    traceback.print_exc()
    sys.exit(1)


def main() -> None:
    # Force immediate output
    print("profile_conversation.py: Starting...", flush=True)
    # Parse LLM config from environment
    llm_config_json = os.environ.get("LLM_CONFIG", "{}")
    llm_config = json.loads(llm_config_json)

    # Get LLM credentials from environment (set by workflow)
    api_key = os.environ.get("LLM_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL")

    llm = LLM(
        model=llm_config.get("model", "gpt-4o-mini"),
        temperature=llm_config.get("temperature", 0.0),
        api_key=api_key,
        base_url=base_url,
    )

    # Set up tools (similar to SWE-bench workload)
    tools = [
        Tool(name=TerminalTool.name),
        Tool(name=FileEditorTool.name),
    ]

    agent = Agent(llm=llm, tools=tools)

    # Create a realistic coding task (SWE-bench style)
    task = (
        "Debug the file at /tmp/test_project/calculator.py - "
        "the divide function needs error handling for division by zero. "
        "First list the directory, then read and fix the file."
    )

    # Set up test directory with a buggy file
    os.makedirs("/tmp/test_project", exist_ok=True)
    with open("/tmp/test_project/calculator.py", "w") as f:
        f.write(
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "def divide(a, b):\n"
            "    # BUG: No error handling\n"
            "    return a / b\n"
        )

    max_iterations = int(os.environ.get("MAX_ITERATIONS", "10"))

    print(
        f"Starting with {len(tools)} tools, model: {llm.model}, max: {max_iterations}"
    )

    # Create conversation with workspace
    cwd = os.getcwd()
    conversation = Conversation(agent=agent, workspace=cwd)

    # Send the task message
    conversation.send_message(task)

    # Run conversation with iteration limit
    conversation.run(max_iterations=max_iterations)

    print("Completed successfully")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        traceback.print_exc()
        sys.exit(1)
