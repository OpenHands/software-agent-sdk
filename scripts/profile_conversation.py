#!/usr/bin/env python3
"""
Profile script for SWE-bench style SDK conversations.

This script creates a realistic coding task to profile the SDK's core code paths:
- LLM API calls
- Tool execution (file operations)
- Conversation iteration loop

Usage:
    LLM_CONFIG='{"model": "...", "temperature": 0.0}' \
    MAX_ITERATIONS=10 \
    python scripts/profile_conversation.py
"""

import json
import os

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


def main() -> None:
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
    main()
