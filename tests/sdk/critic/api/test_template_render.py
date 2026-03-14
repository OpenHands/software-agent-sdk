"""
Regression tests for the chat template implementation.

This file contains sample traces with their expected formatted outputs.
These are used to ensure the chat template implementation remains stable
and produces the same results across versions.

The ground truth was generated using transformers AutoTokenizer with
Qwen/Qwen3-4B-Instruct-2507 tokenizer. The transformers library is NOT
required to run these tests - it's only needed if you want to regenerate
the ground truth values using the --generate-ground-truth flag.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from openhands.sdk.critic.impl.api.chat_template import ChatTemplateRenderer


# =============================================================================
# Test cases with ground truth
# Each test case contains:
#   - messages: The input messages
#   - tools: Optional tool definitions
#   - add_generation_prompt: Whether to add generation prompt
#   - expected: The exact expected output string
# =============================================================================

TEST_CASES: list[dict[str, Any]] = [
    # ------------------------------------------------------------------
    # Test 1: Simple single-turn conversation
    # ------------------------------------------------------------------
    {
        "name": "simple_single_turn",
        "messages": [
            {"role": "user", "content": "Hello!"},
        ],
        "tools": None,
        "add_generation_prompt": False,
        "expected": "<|im_start|>user\nHello!<|im_end|>\n",
    },
    # ------------------------------------------------------------------
    # Test 2: User + Assistant turn
    # ------------------------------------------------------------------
    {
        "name": "user_assistant_turn",
        "messages": [
            {"role": "user", "content": "What is Python?"},
            {
                "role": "assistant",
                "content": "Python is a high-level programming language.",
            },
        ],
        "tools": None,
        "add_generation_prompt": False,
        "expected": (
            "<|im_start|>user\nWhat is Python?<|im_end|>\n"
            "<|im_start|>assistant\n"
            "Python is a high-level programming language.<|im_end|>\n"
        ),
    },
    # ------------------------------------------------------------------
    # Test 3: With system message
    # ------------------------------------------------------------------
    {
        "name": "with_system_message",
        "messages": [
            {"role": "system", "content": "You are a helpful coding assistant."},
            {"role": "user", "content": "Write a hello world in Python."},
            {"role": "assistant", "content": 'print("Hello, World!")'},
        ],
        "tools": None,
        "add_generation_prompt": False,
        "expected": (
            "<|im_start|>system\nYou are a helpful coding assistant.<|im_end|>\n"
            "<|im_start|>user\nWrite a hello world in Python.<|im_end|>\n"
            '<|im_start|>assistant\nprint("Hello, World!")<|im_end|>\n'
        ),
    },
    # ------------------------------------------------------------------
    # Test 4: Multi-turn conversation
    # ------------------------------------------------------------------
    {
        "name": "multi_turn_conversation",
        "messages": [
            {"role": "system", "content": "You are a math tutor."},
            {"role": "user", "content": "What is 2+2?"},
            {"role": "assistant", "content": "2+2 equals 4."},
            {"role": "user", "content": "And 3+3?"},
            {"role": "assistant", "content": "3+3 equals 6."},
        ],
        "tools": None,
        "add_generation_prompt": False,
        "expected": (
            "<|im_start|>system\nYou are a math tutor.<|im_end|>\n"
            "<|im_start|>user\nWhat is 2+2?<|im_end|>\n"
            "<|im_start|>assistant\n2+2 equals 4.<|im_end|>\n"
            "<|im_start|>user\nAnd 3+3?<|im_end|>\n"
            "<|im_start|>assistant\n3+3 equals 6.<|im_end|>\n"
        ),
    },
    # ------------------------------------------------------------------
    # Test 5: With generation prompt
    # ------------------------------------------------------------------
    {
        "name": "with_generation_prompt",
        "messages": [
            {"role": "user", "content": "Tell me a joke."},
        ],
        "tools": None,
        "add_generation_prompt": True,
        "expected": (
            "<|im_start|>user\nTell me a joke.<|im_end|>\n<|im_start|>assistant\n"
        ),
    },
    # ------------------------------------------------------------------
    # Test 6: With single tool
    # ------------------------------------------------------------------
    {
        "name": "with_single_tool",
        "messages": [
            {"role": "user", "content": "What's the weather?"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather info",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }
        ],
        "add_generation_prompt": False,
        "expected": (
            "<|im_start|>system\n# Tools\n\n"
            "You may call one or more functions to assist with the user query.\n\n"
            "You are provided with function signatures within "
            "<tools></tools> XML tags:\n<tools>\n"
            '{"type": "function", "function": {"name": "get_weather", '
            '"description": "Get weather info", "parameters": {"type": "object", '
            '"properties": {"city": {"type": "string"}}, "required": ["city"]}}}\n'
            "</tools>\n\n"
            "For each function call, return a json object with function name "
            "and arguments within <tool_call></tool_call> XML tags:\n"
            "<tool_call>\n"
            '{"name": <function-name>, "arguments": <args-json-object>}\n'
            "</tool_call><|im_end|>\n"
            "<|im_start|>user\nWhat's the weather?<|im_end|>\n"
        ),
    },
    # ------------------------------------------------------------------
    # Test 7: With tools and system message
    # ------------------------------------------------------------------
    {
        "name": "tools_with_system_message",
        "messages": [
            {"role": "system", "content": "You are a weather assistant."},
            {"role": "user", "content": "Check weather in Tokyo."},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                    },
                },
            }
        ],
        "add_generation_prompt": False,
        "expected": (
            "<|im_start|>system\nYou are a weather assistant.\n\n# Tools\n\n"
            "You may call one or more functions to assist with the user query.\n\n"
            "You are provided with function signatures within "
            "<tools></tools> XML tags:\n<tools>\n"
            '{"type": "function", "function": {"name": "get_weather", '
            '"description": "Get weather", "parameters": {"type": "object", '
            '"properties": {"city": {"type": "string"}}}}}\n'
            "</tools>\n\n"
            "For each function call, return a json object with function name "
            "and arguments within <tool_call></tool_call> XML tags:\n"
            "<tool_call>\n"
            '{"name": <function-name>, "arguments": <args-json-object>}\n'
            "</tool_call><|im_end|>\n"
            "<|im_start|>user\nCheck weather in Tokyo.<|im_end|>\n"
        ),
    },
    # ------------------------------------------------------------------
    # Test 8: Code content with special characters
    # ------------------------------------------------------------------
    {
        "name": "code_with_special_chars",
        "messages": [
            {
                "role": "user",
                "content": "```python\ndef foo():\n    return {'key': 'value'}\n```",
            },
            {"role": "assistant", "content": "This function returns a dictionary."},
        ],
        "tools": None,
        "add_generation_prompt": False,
        "expected": (
            "<|im_start|>user\n```python\ndef foo():\n    return {'key': 'value'}\n"
            "```<|im_end|>\n<|im_start|>assistant\n"
            "This function returns a dictionary.<|im_end|>\n"
        ),
    },
    # ------------------------------------------------------------------
    # Test 9: Unicode and emoji content
    # ------------------------------------------------------------------
    {
        "name": "unicode_and_emoji",
        "messages": [
            {"role": "user", "content": "Translate: 你好 🌍"},
            {"role": "assistant", "content": "Hello 🌍"},
        ],
        "tools": None,
        "add_generation_prompt": False,
        "expected": (
            "<|im_start|>user\nTranslate: 你好 🌍<|im_end|>\n"
            "<|im_start|>assistant\nHello 🌍<|im_end|>\n"
        ),
    },
    # ------------------------------------------------------------------
    # Test 10: Long multi-paragraph content
    # ------------------------------------------------------------------
    {
        "name": "long_multi_paragraph",
        "messages": [
            {
                "role": "system",
                "content": "You are a writing assistant.\n\nBe concise and clear.",
            },
            {"role": "user", "content": "Paragraph 1.\n\nParagraph 2.\n\nParagraph 3."},
        ],
        "tools": None,
        "add_generation_prompt": False,
        "expected": (
            "<|im_start|>system\nYou are a writing assistant.\n\n"
            "Be concise and clear.<|im_end|>\n"
            "<|im_start|>user\nParagraph 1.\n\nParagraph 2.\n\n"
            "Paragraph 3.<|im_end|>\n"
        ),
    },
    # ------------------------------------------------------------------
    # Test 11: Multiple tools
    # ------------------------------------------------------------------
    {
        "name": "multiple_tools",
        "messages": [
            {"role": "user", "content": "Help me search and save."},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "description": "Search",
                    "parameters": {
                        "type": "object",
                        "properties": {"q": {"type": "string"}},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "save",
                    "description": "Save",
                    "parameters": {
                        "type": "object",
                        "properties": {"data": {"type": "string"}},
                    },
                },
            },
        ],
        "add_generation_prompt": False,
        "expected": (
            "<|im_start|>system\n# Tools\n\n"
            "You may call one or more functions to assist with the user query.\n\n"
            "You are provided with function signatures within "
            "<tools></tools> XML tags:\n<tools>\n"
            '{"type": "function", "function": {"name": "search", '
            '"description": "Search", "parameters": {"type": "object", '
            '"properties": {"q": {"type": "string"}}}}}\n'
            '{"type": "function", "function": {"name": "save", '
            '"description": "Save", "parameters": {"type": "object", '
            '"properties": {"data": {"type": "string"}}}}}\n'
            "</tools>\n\n"
            "For each function call, return a json object with function name "
            "and arguments within <tool_call></tool_call> XML tags:\n"
            "<tool_call>\n"
            '{"name": <function-name>, "arguments": <args-json-object>}\n'
            "</tool_call><|im_end|>\n"
            "<|im_start|>user\nHelp me search and save.<|im_end|>\n"
        ),
    },
    # ------------------------------------------------------------------
    # Test 12: Empty content
    # ------------------------------------------------------------------
    {
        "name": "empty_content",
        "messages": [
            {"role": "user", "content": ""},
            {"role": "assistant", "content": "Your message is empty."},
        ],
        "tools": None,
        "add_generation_prompt": False,
        "expected": (
            "<|im_start|>user\n<|im_end|>\n"
            "<|im_start|>assistant\nYour message is empty.<|im_end|>\n"
        ),
    },
    # ------------------------------------------------------------------
    # Test 13: Realistic agent trace (critic use case)
    # ------------------------------------------------------------------
    {
        "name": "realistic_agent_trace",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a coding assistant helping with "
                    "software development tasks."
                ),
            },
            {"role": "user", "content": "Create a function to calculate factorial."},
            {
                "role": "assistant",
                "content": (
                    "I'll create a factorial function for you.\n\n```python\n"
                    "def factorial(n):\n    if n <= 1:\n        return 1\n"
                    "    return n * factorial(n - 1)\n```\n\n"
                    "This is a recursive implementation."
                ),
            },
            {"role": "user", "content": "Can you add input validation?"},
            {
                "role": "assistant",
                "content": (
                    "Here's the updated function with validation:\n\n```python\n"
                    "def factorial(n):\n"
                    "    if not isinstance(n, int):\n"
                    '        raise TypeError("Input must be an integer")\n'
                    "    if n < 0:\n"
                    '        raise ValueError("Input must be non-negative")\n'
                    "    if n <= 1:\n        return 1\n"
                    "    return n * factorial(n - 1)\n```"
                ),
            },
        ],
        "tools": None,
        "add_generation_prompt": False,
        "expected": (
            "<|im_start|>system\n"
            "You are a coding assistant helping with software development tasks."
            "<|im_end|>\n"
            "<|im_start|>user\nCreate a function to calculate factorial.<|im_end|>\n"
            "<|im_start|>assistant\n"
            "I'll create a factorial function for you.\n\n```python\n"
            "def factorial(n):\n    if n <= 1:\n        return 1\n"
            "    return n * factorial(n - 1)\n```\n\n"
            "This is a recursive implementation.<|im_end|>\n"
            "<|im_start|>user\nCan you add input validation?<|im_end|>\n"
            "<|im_start|>assistant\n"
            "Here's the updated function with validation:\n\n```python\n"
            "def factorial(n):\n"
            "    if not isinstance(n, int):\n"
            '        raise TypeError("Input must be an integer")\n'
            "    if n < 0:\n"
            '        raise ValueError("Input must be non-negative")\n'
            "    if n <= 1:\n        return 1\n"
            "    return n * factorial(n - 1)\n```<|im_end|>\n"
        ),
    },
]


@pytest.fixture
def renderer(qwen3_tokenizer_config_path):
    """Create a ChatTemplateRenderer for testing."""
    with qwen3_tokenizer_config_path.open(encoding="utf-8") as handle:
        tokenizer_config = json.load(handle)
    return ChatTemplateRenderer(chat_template=tokenizer_config["chat_template"])


@pytest.mark.parametrize("test_case", TEST_CASES, ids=[tc["name"] for tc in TEST_CASES])
def test_chat_template_regression(
    renderer: ChatTemplateRenderer, test_case: dict[str, Any]
):
    """
    Regression test for chat template rendering.

    Compares the output of our implementation against ground truth
    generated from transformers AutoTokenizer.
    """
    messages = test_case["messages"]
    tools = test_case.get("tools")
    add_generation_prompt = test_case.get("add_generation_prompt", False)
    expected = test_case["expected"]

    actual = renderer.apply_chat_template(
        messages=messages,
        tools=tools,
        add_generation_prompt=add_generation_prompt,
    )

    assert actual == expected, (
        f"\nExpected ({len(expected)} chars):\n"
        f"  {repr(expected[:200])}{'...' if len(expected) > 200 else ''}\n"
        f"Actual ({len(actual)} chars):\n"
        f"  {repr(actual[:200])}{'...' if len(actual) > 200 else ''}"
    )


def generate_ground_truth(tokenizer_name: str = "Qwen/Qwen3-4B-Instruct-2507") -> None:
    """
    Generate ground truth using transformers library.

    This function is used to update the expected values in TEST_CASES
    when needed (e.g., when adding new test cases).

    Requires transformers to be installed: pip install transformers
    """
    try:
        from transformers import AutoTokenizer  # type: ignore
        # This dependency is not included in pyproject.toml by default
        # to avoid bloating the installation for users who don't need it.
    except ImportError as e:
        raise ImportError(
            "transformers is required to generate ground truth. "
            "Install it with: pip install transformers"
        ) from e

    tokenizer = AutoTokenizer.from_pretrained(tokenizer_name)

    print("# Generated ground truth values:")
    print("# Copy these into TEST_CASES if updating expected values")
    print()

    for test_case in TEST_CASES:
        name = test_case["name"]
        messages = test_case["messages"]
        tools = test_case.get("tools")
        add_generation_prompt = test_case.get("add_generation_prompt", False)

        if tools:
            output = tokenizer.apply_chat_template(
                messages,
                tools=tools,
                tokenize=False,
                add_generation_prompt=add_generation_prompt,
            )
        else:
            output = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=add_generation_prompt
            )

        print(f"# Test: {name}")
        print(f'"expected": {repr(output)},')
        print()


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description=(
            "Chat template tests - use pytest to run tests, or "
            "--generate-ground-truth to regenerate expected values"
        )
    )
    parser.add_argument(
        "--generate-ground-truth",
        action="store_true",
        help=(
            "Generate ground truth values using transformers library "
            "(requires transformers)"
        ),
    )
    parser.add_argument(
        "--tokenizer",
        default="Qwen/Qwen3-4B-Instruct-2507",
        help="Tokenizer name to use",
    )

    args = parser.parse_args()

    if args.generate_ground_truth:
        generate_ground_truth(args.tokenizer)
    else:
        print(
            "Use pytest to run tests: "
            "pytest tests/sdk/critic/api/test_template_render.py"
        )
        print("Or use --generate-ground-truth to regenerate expected values")
        sys.exit(1)
