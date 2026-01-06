# Integration Tests

This directory contains integration tests for the agent-sdk that use real LLM calls to test end-to-end functionality.

## Overview

The integration tests are designed to verify that the agent-sdk works correctly with real LLM models by running complete workflows. Each test creates a temporary environment, provides the agent with specific tools, gives it an instruction, and then verifies the results.

### Test Types

Tests are classified into two types based on their filename prefix:

- **Integration tests** (`t*.py`) - **REQUIRED**: Verify that the agent successfully completes essential tasks. These tests must pass for releases and focus on task completion and outcomes.
- **Behavior tests** (`b*.py`) - **OPTIONAL**: Verify that the agent follows system message guidelines and best practices. These tests track quality improvements and focus on how the agent approaches problems. Failures don't block releases but should be addressed for optimal user experience.

Success rates are calculated separately for each test type to track both completion capability and behavior quality.

See [BEHAVIOR_TESTS.md](BEHAVIOR_TESTS.md) for more details on behavior testing.

## Directory Structure

```
tests/integration/
├── README.md                    # This file
├── BEHAVIOR_TESTS.md            # Documentation for behavior testing framework
├── __init__.py                  # Package initialization
├── base.py                      # Base classes for integration tests
├── run_infer.py                 # Main test runner script
├── run_infer.sh                 # Shell script wrapper for running tests
├── outputs/                     # Test results and reports (auto-generated)
├── tests/                       # Individual test files
│   ├── t*.py                    # Task completion tests (critical)
│   └── b*.py                    # Agent behavior tests (ux)
└── utils/                       # Test utilities (e.g., llm_judge.py)
```

## Running Integration Tests

### From github

The easiest way to run the integration tests if from github by tagging the label `integration-test` to your pull request.
A pull request comment will notify you as soon as the tests have been executed.
The results of the tests (and all of the logs) will be downloadable using a link added in the comment.

### Locally

```bash
# Run all tests
uv run python tests/integration/run_infer.py --llm-config '{"model": "litellm_proxy/anthropic/claude-sonnet-4-5-20250929"}'

# Run a specific test
uv run python tests/integration/run_infer.py --llm-config '{"model": "litellm_proxy/anthropic/claude-sonnet-4-5-20250929"}' --eval-ids t01_fix_simple_typo

# Run with LLM message recording (for debugging)
LLM_MESSAGES_DIR=./llm_messages uv run python tests/integration/run_infer.py --llm-config '{"model": "litellm_proxy/anthropic/claude-sonnet-4-5-20250929"}'
```

### Recording LLM Completions

For debugging and analysis, you can record all LLM request/response messages by setting the `LLM_MESSAGES_DIR` environment variable:

```bash
# Record LLM messages to a specific directory
export LLM_MESSAGES_DIR=/path/to/store/messages
uv run python tests/integration/run_infer.py --llm-config '{"model": "..."}'
```

When enabled, this feature:
- Saves all LLM messages from each test to JSON files (`{test_id}_llm_messages.json`)
- Stores files in the specified directory
- Copies them to the test output directory alongside logs
- Does not record anything if the environment variable is not set

This is useful for:
- Debugging test failures by inspecting exact LLM inputs/outputs
- Analyzing agent behavior across multiple conversation turns
- Creating datasets for evaluation or training

## Automated Testing with GitHub Actions

The integration tests are automatically executed via GitHub Actions using the workflow defined in `.github/workflows/integration-runner.yml`.

### Workflow Triggers

The GitHub workflow runs integration tests in the following scenarios:

1. **Pull Request Labels**: When a PR is labeled with `integration-test`
2. **Manual Trigger**: Via workflow dispatch with a required reason
3. **Scheduled Runs**: Daily at 10:30 PM UTC (cron: `30 22 * * *`)

## Available Tests

### Integration Tests (`t*.py`) - **Required**

These tests must pass for releases and verify that the agent can successfully complete essential tasks:

- **t01_fix_simple_typo** - Tests that the agent can fix typos in a file
- **t02_add_bash_hello** - Tests that the agent can execute bash commands
- **t03_jupyter_write_file** - Tests Jupyter notebook integration
- **t04_git_staging** - Tests git operations
- **t05_simple_browsing** - Tests web browsing capabilities
- **t06_github_pr_browsing** - Tests GitHub PR browsing
- **t07_interactive_commands** - Tests interactive command handling
- **t08_image_file_viewing** - Tests image file viewing capabilities
- **t09_token_condenser** - Tests that token-based condensation works correctly by verifying `get_token_count()` triggers condensation when token limits are exceeded
- **t10_hard_context_reset** - Tests that the agent can continue working correctly after context condensation. Uses `run_conversation()` override for multi-phase testing

### Behavior Tests (`b*.py`) - **Optional**

These tests track quality improvements and don't block releases. They verify that agents follow system message guidelines and handle complex, nuanced scenarios appropriately:

- **b01_no_premature_implementation** - Tests that the agent doesn't start implementing when asked for advice. Uses a real codebase (software-agent-sdk checked out to a historical commit) to test that the agent explores, provides suggestions, and asks clarifying questions instead of immediately creating or editing files.

For more details on behavior testing and guidelines for adding new tests, see [BEHAVIOR_TESTS.md](BEHAVIOR_TESTS.md).

## Writing Integration Tests

All integration tests inherit from `BaseIntegrationTest` in `base.py`. The base class provides a consistent framework with several customizable properties:

### Required Methods

- **`tools`** (property) - List of tools available to the agent
- **`setup()`** - Initialize test-specific setup (create files, etc.)
- **`verify_result()`** - Verify the test succeeded and return `TestResult`

### Optional Properties

- **`condenser`** (property) - Optional condenser configuration for the agent (default: `None`)
  - Override to test condensation or manage long conversations
  - Example: `t09_token_condenser` uses this to verify token counting
- **`max_iteration_per_run`** (property) - Maximum iterations per conversation (default: `100`)
  - Override to limit LLM calls for faster tests
  - Useful for tests that should complete quickly

### Optional Methods

- **`run_conversation()`** - Execute the conversation with the agent (new in v1.7.5)
  - Override this method to customize conversation flow for multi-step tests
  - Default implementation sends a single instruction and runs to completion
  - Provides access to `self.conversation` for direct manipulation
  - Example use cases:
    - Send multiple messages in sequence
    - Verify intermediate state between conversation phases
    - Test complex multi-turn interactions
    - Trigger condensation at specific points

#### Example: Multi-Step Test

```python
class MultiStepTest(BaseIntegrationTest):
    def run_conversation(self) -> None:
        # Step 1: Initial task
        self.conversation.send_message(
            Message(role="user", content=[TextContent(text="First, create a file")])
        )
        self.conversation.run()

        # Intermediate verification
        assert os.path.exists(os.path.join(self.workspace, "file.txt"))

        # Step 2: Follow-up task
        self.conversation.send_message(
            Message(role="user", content=[TextContent(text="Now modify the file")])
        )
        self.conversation.run()
```

See `t10_hard_context_reset.py` for a real example of overriding `run_conversation()`.
