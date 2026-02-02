# Integration Tests

This directory contains integration tests for the agent-sdk that use real LLM calls to test end-to-end functionality.

## Overview

The integration tests are designed to verify that the agent-sdk works correctly with real LLM models by running complete workflows. Each test creates a temporary environment, provides the agent with specific tools, gives it an instruction, and then verifies the results.

### Test Types

Tests are classified into three types based on their filename prefix:

- **Integration tests** (`t*.py`) - **REQUIRED**: Verify that the agent successfully completes essential tasks. These tests must pass for releases and focus on task completion and outcomes.
- **Behavior tests** (`b*.py`) - **OPTIONAL**: Verify that the agent follows system message guidelines and best practices. These tests track quality improvements and focus on how the agent approaches problems. Failures don't block releases but should be addressed for optimal user experience.
- **Condenser tests** (`c*.py`) - **OPTIONAL, NON-BLOCKING**: Stress test the condensation system's interaction with LLM APIs to ensure compatibility. These tests run on a limited set of LLMs (currently Claude Opus 4.5 and GPT-5.1 Codex Max) and are triggered separately from integration tests. They validate that conversation condensation works correctly across different models and API patterns.

Success rates are calculated separately for each test type to track completion capability, behavior quality, and condenser reliability.

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
│   ├── t*.py                    # Task completion tests (required)
│   ├── b*.py                    # Agent behavior tests (optional)
│   └── c*.py                    # Condenser stress tests (optional, non-blocking)
└── utils/                       # Test utilities (e.g., llm_judge.py)
```

## Running Integration Tests

### From github

The easiest way to run the integration tests if from github by tagging the label `integration-test` to your pull request.
A pull request comment will notify you as soon as the tests have been executed.
The results of the tests (and all of the logs) will be downloadable using a link added in the comment.

For condenser tests, use the `condenser-test` label instead.

### Locally

```bash
# Run all tests
uv run python tests/integration/run_infer.py --llm-config '{"model": "litellm_proxy/anthropic/claude-sonnet-4-5-20250929"}'

# Run a specific test
uv run python tests/integration/run_infer.py --llm-config '{"model": "litellm_proxy/anthropic/claude-sonnet-4-5-20250929"}' --eval-ids t01_fix_simple_typo

# Run only condenser tests
uv run python tests/integration/run_infer.py --llm-config '{"model": "litellm_proxy/anthropic/claude-opus-4-5", "extended_thinking": true}' --test-type condenser
```

## Automated Testing with GitHub Actions

Tests are automatically executed via GitHub Actions using two separate workflows:

### Integration/Behavior Tests Workflow

Defined in `.github/workflows/integration-runner.yml`, this workflow runs integration and behavior tests.

**Triggers:**
1. **Pull Request Labels**: When a PR is labeled with `integration-test` or `behavior-test`
2. **Manual Trigger**: Via workflow dispatch with a required reason
3. **Scheduled Runs**: Daily at 10:30 PM UTC (cron: `30 22 * * *`)

**Test Coverage:** Runs across 6 LLM models (Claude Sonnet 4.5, GPT-5.1 Codex Max, Deepseek, Kimi K2, Gemini 3 Pro, Devstral 2512)

### Condenser Tests Workflow

Defined in `.github/workflows/condenser-runner.yml`, this workflow runs condenser stress tests separately.

**Triggers:**
1. **Pull Request Labels**: When a PR is labeled with `condenser-test`
2. **Manual Trigger**: Via workflow dispatch with a required reason

**Test Coverage:** Runs only against 2 LLMs (Claude Opus 4.5 with extended thinking, GPT-5.1 Codex Max) to save costs while validating cross-model compatibility

**Note:** Condenser tests are non-blocking and do not prevent PR merges

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

### Behavior Tests (`b*.py`) - **Optional**

These tests track quality improvements and don't block releases. They verify that agents follow system message guidelines and handle complex, nuanced scenarios appropriately:

- **b01_no_premature_implementation** - Tests that the agent doesn't start implementing when asked for advice. Uses a real codebase (software-agent-sdk checked out to a historical commit) to test that the agent explores, provides suggestions, and asks clarifying questions instead of immediately creating or editing files.

For more details on behavior testing and guidelines for adding new tests, see [BEHAVIOR_TESTS.md](BEHAVIOR_TESTS.md).

### Condenser Tests (`c*.py`) - **Optional, Non-Blocking**

These tests stress test the condensation system's interaction with LLM APIs to ensure compatibility across different models. Unlike integration tests, condenser tests run on a limited set of LLMs (currently Claude Opus 4.5 and GPT-5.1 Codex Max) to save costs while validating cross-model compatibility. They are triggered separately using the `condenser-test` label and do not block PR merges.

**Purpose:** Validate that conversation condensation works correctly across different models and API patterns, particularly focusing on:
- Model-specific features (e.g., thinking blocks in Claude Opus)
- Condensation triggers (token limits, event counts, explicit requests)
- Conversation history management
- API signature compatibility after condensation

**Current Tests:**

- **c01_thinking_block_condenser** - Tests that Claude Opus's thinking blocks are properly handled during condensation. Verifies that:
  - Multiple thinking blocks are generated across a multi-step conversation
  - Condensation is triggered correctly
  - The first thinking block is forgotten during condensation
  - Later thinking blocks are preserved after condensation
  - No malformed signature errors occur when condensed history is sent to the API
- **c02_hard_context_reset** - Tests hard context reset when condensation is unavailable. Verifies that:
  - Explicit condense() calls trigger a hard context reset when no valid range exists
  - The hard context reset condenses all events in the view (summary_offset=0)
  - The conversation can continue successfully after the hard context reset
- **c03_delayed_condensation** - Tests delayed condensation with soft requirements. Verifies that:
  - Soft requirements (resource limits) gracefully continue when condensation is unavailable
  - Conversation continues without crashing when condensation can't be satisfied
  - Condensation succeeds once multiple atomic units make it available
- **c04_token_condenser** - Tests that token-based condensation works correctly. Verifies that:
  - An agent can be configured with LLMSummarizingCondenser using max_tokens
  - The condenser correctly uses get_token_count to measure conversation size
  - Condensation is triggered when token limit is exceeded
- **c05_size_condenser** - Tests that size-based condensation works correctly. Verifies that:
  - An agent can be configured with LLMSummarizingCondenser using max_size
  - The condenser correctly counts events to measure conversation size
  - Condensation is triggered when event count limit is exceeded

## Writing Integration Tests

All integration tests inherit from `BaseIntegrationTest` in `base.py`. The base class provides a consistent framework with several customizable properties:

### Required Methods

- **`tools`** (property) - List of tools available to the agent
- **`setup()`** - Initialize test-specific setup (create files, etc.)
- **`verify_result()`** - Verify the test succeeded and return `TestResult`

### Optional Properties

- **`condenser`** (property) - Optional condenser configuration for the agent (default: `None`)
  - Override to test condensation or manage long conversations
  - Example: `c04_token_condenser` uses this to verify token counting
- **`max_iteration_per_run`** (property) - Maximum iterations per conversation (default: `100`)
  - Override to limit LLM calls for faster tests
  - Useful for tests that should complete quickly

### Conversation Control

The standard way to define an integration test is to set the `INSTRUCTION` class variable. These instructions are sent to the agent as the first user message.

However, if the functionality being tested requires multiple instructions or accessing the conversation object mid-test then the test can instead be defined by overriding the `run_instructions` method. This method provides a `LocalConversation` object that can be manipulated directly by sending messages, triggering condensations, and the like.