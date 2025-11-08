"""Test data generator utility for creating LLM completion test fixtures.

This utility is based on examples/hello_world.py and can be used to regenerate
test assets when the LLM implementation changes.
"""

import json
import shutil
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Agent,
    Conversation,
    Event,
    LLMConvertibleEvent,
    Message,
    TextContent,
    get_logger,
)
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


logger = get_logger(__name__)


def get_output_dir(output_dir: Path | None = None) -> Path:
    """Get output directory, creating if needed."""
    dir_path = Path(__file__).parent
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path


def create_llm(
    api_key: str,
    base_url: str,
    model: str,
    log_completions_folder: str | None = None,
    **kwargs,
) -> LLM:
    """Create an LLM instance for data generation."""
    llm_kwargs = {
        "model": model,
        "base_url": base_url,
        "api_key": SecretStr(api_key),
        "log_completions": True,
        **kwargs,
    }
    if log_completions_folder:
        llm_kwargs["log_completions_folder"] = log_completions_folder
    return LLM(**llm_kwargs, usage_id="test-llm")


def create_tools(working_dir: str | None = None) -> list[Tool]:
    """Create standard tool specifications for testing."""
    register_tool("TerminalTool", TerminalTool)
    register_tool("FileEditorTool", FileEditorTool)
    return [
        Tool(name="TerminalTool"),
        Tool(name="FileEditorTool"),
    ]


def run_conversation(
    api_key: str,
    base_url: str,
    model: str,
    user_message: str,
    output_dir: Path,
    output_filename: str,
    log_completions_folder: str | None = None,
) -> list[dict[str, Any]]:
    """Run a conversation and collect LLM messages."""
    llm = create_llm(api_key, base_url, model, log_completions_folder)
    tools = create_tools()
    agent = Agent(llm=llm, tools=tools)

    llm_messages = []

    def conversation_callback(event: Event):
        logger.info(f"Found a conversation message: {str(event)[:200]}...")
        if isinstance(event, LLMConvertibleEvent):
            llm_messages.append(event.to_llm_message().to_chat_dict())

    conversation = Conversation(agent=agent, callbacks=[conversation_callback])
    message = Message(role="user", content=[TextContent(text=user_message)])
    conversation.send_message(message=message)
    conversation.run()

    output_path = output_dir / output_filename
    with open(output_path, "w") as f:
        json.dump(llm_messages, f, indent=2)

    logger.info(f"Saved {len(llm_messages)} messages to {output_path}")
    return llm_messages


def generate_test_data(
    api_key: str,
    base_url: str,
    model: str,
    user_message: str,
    output_dir: Path,
    is_function_calling: bool,
) -> list[dict[str, Any]]:
    """Generate test data for a specific model type."""
    data_type = "function calling" if is_function_calling else "non-function calling"
    logger.info(f"Generating {data_type} data with model: {model}")

    log_folder = "llm-logs" if is_function_calling else "nonfncall-llm-logs"
    output_file = (
        "fncall-llm-message.json"
        if is_function_calling
        else "nonfncall-llm-message.json"
    )

    return run_conversation(
        api_key=api_key,
        base_url=base_url,
        model=model,
        user_message=user_message,
        output_dir=output_dir,
        output_filename=output_file,
        log_completions_folder=log_folder,
    )


def copy_log_files(output_dir: Path):
    """Copy log files from current directory to fixtures directory."""
    current_dir = Path.cwd()

    log_configs = [
        ("llm-logs", "llm-logs"),
        ("nonfncall-llm-logs", "nonfncall-llm-logs"),
    ]

    for src_name, dst_name in log_configs:
        src_path = current_dir / src_name
        dst_path = output_dir / dst_name
        if src_path.exists():
            if dst_path.exists():
                shutil.rmtree(dst_path)
            shutil.copytree(src_path, dst_path)
            shutil.rmtree(src_path)
            logger.info(f"Copied {src_name} logs to {dst_path}")


def validate_message_files(output_dir: Path) -> bool:
    """Validate message files exist and have correct structure."""
    files = [
        output_dir / "fncall-llm-message.json",
        output_dir / "nonfncall-llm-message.json",
    ]

    for file_path in files:
        if not file_path.exists():
            logger.error(f"Message file not found: {file_path}")
            return False

        with open(file_path) as f:
            messages = json.load(f)

        if not isinstance(messages, list) or len(messages) == 0:
            logger.error(f"Invalid messages in {file_path}")
            return False

        for msg in messages:
            if not isinstance(msg, dict) or "role" not in msg or "content" not in msg:
                logger.error(f"Invalid message structure in {file_path}")
                return False

    return True


def validate_log_directories(output_dir: Path) -> bool:
    """Validate log directories exist and contain files."""
    log_dirs = [
        output_dir / "llm-logs",
        output_dir / "nonfncall-llm-logs",
    ]

    for log_dir in log_dirs:
        if not log_dir.exists():
            logger.error(f"Log directory not found: {log_dir}")
            return False

        log_files = list(log_dir.glob("*.json"))
        if len(log_files) == 0:
            logger.error(f"No log files found in {log_dir}")
            return False

    return True


def validate_generated_data(output_dir: Path) -> bool:
    """Validate that generated data has expected structure."""
    try:
        return validate_message_files(output_dir) and validate_log_directories(
            output_dir
        )
    except Exception as e:
        logger.error(f"Validation failed: {e}")
        return False


def generate_all_test_data(
    api_key: str,
    base_url: str = "https://llm-proxy.eval.all-hands.dev",
    output_dir: Path | None = None,
    fncall_model: str = "litellm_proxy/anthropic/claude-sonnet-4-20250514",
    nonfncall_model: str = "litellm_proxy/deepseek/deepseek-chat",
    user_message: str = (
        "Hello! Can you create a new Python file named hello.py that prints "
        "'Hello, World!'?"
    ),
) -> dict[str, list[dict[str, Any]]]:
    """Generate all test data."""
    logger.info("Generating all test data...")

    output_path = get_output_dir(output_dir)

    fncall_messages = generate_test_data(
        api_key=api_key,
        base_url=base_url,
        model=fncall_model,
        user_message=user_message,
        output_dir=output_path,
        is_function_calling=True,
    )

    nonfncall_messages = generate_test_data(
        api_key=api_key,
        base_url=base_url,
        model=nonfncall_model,
        user_message=user_message,
        output_dir=output_path,
        is_function_calling=False,
    )

    logger.info("Test data generation complete!")

    return {
        "function_calling": fncall_messages,
        "non_function_calling": nonfncall_messages,
    }


def main():
    """Main function for command-line usage."""
    import argparse

    parser = argparse.ArgumentParser(description="Generate LLM test data")
    parser.add_argument(
        "--api-key",
        help=(
            "API key for LLM service (required for generation, optional for validation)"
        ),
    )
    parser.add_argument(
        "--base-url",
        default="https://llm-proxy.eval.all-hands.dev",
        help="Base URL for LLM service",
    )
    parser.add_argument("--output-dir", help="Output directory for test data")
    parser.add_argument(
        "--fncall-model",
        default="litellm_proxy/anthropic/claude-sonnet-4-20250514",
        help="Function calling model",
    )
    parser.add_argument(
        "--nonfncall-model",
        default="litellm_proxy/deepseek/deepseek-chat",
        help="Non-function calling model",
    )
    parser.add_argument(
        "--user-message",
        default=(
            "Hello! Can you create a new Python file named hello.py that prints "
            "'Hello, World!'?"
        ),
        help="User message for conversation",
    )
    parser.add_argument(
        "--validate-only", action="store_true", help="Only validate existing data"
    )

    args = parser.parse_args()
    output_dir = Path(args.output_dir) if args.output_dir else None

    if args.validate_only:
        output_path = get_output_dir(output_dir)
        if validate_generated_data(output_path):
            print("✅ Test data validation passed")
        else:
            print("❌ Test data validation failed")
            exit(1)
        return

    if not args.api_key:
        parser.error("--api-key is required for data generation")

    try:
        generate_all_test_data(
            api_key=args.api_key,
            base_url=args.base_url,
            output_dir=output_dir,
            fncall_model=args.fncall_model,
            nonfncall_model=args.nonfncall_model,
            user_message=args.user_message,
        )

        output_path = get_output_dir(output_dir)
        copy_log_files(output_path)

        if validate_generated_data(output_path):
            print("✅ Test data generation and validation completed successfully")
        else:
            print("❌ Test data generation completed but validation failed")
            exit(1)

    except Exception as e:
        logger.error(f"Test data generation failed: {e}")
        print(f"❌ Test data generation failed: {e}")
        exit(1)


if __name__ == "__main__":
    main()
