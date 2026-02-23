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
import sys

from openhands.sdk import LLM, Agent, Conversation
from openhands.tools.preset.default import get_default_tools


def main() -> None:
    # Parse LLM config from environment
    llm_config_json = os.environ.get("LLM_CONFIG", "{}")
    llm_config = json.loads(llm_config_json)

    llm = LLM(
        model=llm_config.get("model", "gpt-4o-mini"),
        temperature=llm_config.get("temperature", 0.0),
    )

    # Get default tools (similar to SWE-bench workload)
    tools = get_default_tools()

    agent = Agent(
        llm=llm,
        tools=tools,
        system_message="You are a coding assistant. Help debug and fix issues.",
    )

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

    print(f"Starting with {len(tools)} tools, model: {llm.model}, max: {max_iterations}")

    conversation = Conversation(agent=agent, initial_message=task)

    # Run conversation with iteration limit
    for i, response in enumerate(conversation):
        print(f"Iteration {i + 1}")
        if i >= max_iterations - 1:
            print(f"Reached max iterations ({max_iterations})")
            break

    print("Completed successfully")


if __name__ == "__main__":
    main()
