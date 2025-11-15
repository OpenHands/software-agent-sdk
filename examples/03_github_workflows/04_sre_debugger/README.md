# SRE Error Debugger

This example demonstrates how to use the OpenHands agent SDK to automatically analyze test failures and debug errors. The agent examines test output, investigates the codebase, identifies root causes, and suggests fixes.

## Overview

**Use Case:** *"An SRE system that reads your server logs and your codebase, then uses this info to debug new errors that are appearing in prod"*

The agent:
- Runs pytest to capture test failures (or reads existing test output)
- Parses error messages, stack traces, and assertion failures
- Investigates the failing test code and source code being tested
- Checks recent git commits that may have introduced bugs
- Generates a detailed error analysis report with root causes and fixes

## Files

- **`workflow.yml`**: GitHub Actions workflow file (trigger on test failures)
- **`agent_script.py`**: Python script that runs the OpenHands agent for error debugging
- **`prompt.py`**: The prompt template instructing the agent on error analysis
- **`test_local.py`**: Local testing script for quick validation
- **`README.md`**: This documentation file

## Features

- **Automatic Test Execution**: Runs pytest and captures failures
- **Root Cause Analysis**: Identifies the actual cause, not just symptoms
- **Code Investigation**: Reads relevant source code and tests
- **Git History Analysis**: Checks recent commits that may have introduced bugs
- **Prioritization**: Categorizes issues as Critical/High/Medium/Low
- **Actionable Fixes**: Provides code examples showing how to fix issues
- **Grouping**: Identifies when multiple failures share the same root cause

## Setup

### 1. Copy the workflow file

Copy `workflow.yml` to `.github/workflows/sre-debugger.yml` in your repository:

```bash
cp examples/03_github_workflows/04_sre_debugger/workflow.yml .github/workflows/sre-debugger.yml
```

### 2. Configure secrets

Set the following secret in your GitHub repository settings:

- **`LLM_API_KEY`** (required): Your LLM API key
  - Get one from the [OpenHands LLM Provider](https://docs.all-hands.dev/openhands/usage/llms/openhands-llms)

**Note**: The workflow automatically uses the `GITHUB_TOKEN` secret.

### 3. Customize the workflow (optional)

Edit `.github/workflows/sre-debugger.yml` to customize configuration:

```yaml
env:
    # Optional: Use a different LLM model
    LLM_MODEL: openhands/claude-sonnet-4-5-20250929
    # Optional: Use a custom LLM base URL
    # LLM_BASE_URL: 'https://custom-api.example.com'
    # Optional: Customize test path
    TEST_PATH: tests/
```

## Usage

### Automatic Trigger on Test Failures

The workflow is configured to run automatically when tests fail in CI:

```yaml
on:
  workflow_run:
    workflows: ["Tests"]  # Name of your test workflow
    types: [completed]

jobs:
  debug-failures:
    if: ${{ github.event.workflow_run.conclusion == 'failure' }}
```

When your test workflow fails, this will automatically:
1. Checkout the code
2. Run the debugger on the failed tests
3. Generate ERROR_ANALYSIS.md
4. Upload it as an artifact

### Manual Trigger

You can also trigger the debugger manually:

1. Go to your repository → Actions → "SRE Error Debugger"
2. Click "Run workflow"
3. (Optional) Specify a test path to debug
4. Click "Run workflow"

### Local Testing

Test the debugger locally before deploying:

```bash
# Set your API key
export LLM_API_KEY="your-api-key"

# Run the test script (will run tests and analyze failures)
cd /path/to/your/repo
uv run python /path/to/agent-sdk/examples/03_github_workflows/04_sre_debugger/test_local.py
```

**Optional environment variables:**
```bash
export TEST_PATH="tests/sdk/"                           # Default: tests/
export TEST_OUTPUT_FILE="test_failures.log"             # Skip test run, use existing output
export LLM_MODEL="openhands/claude-sonnet-4-5-20250929" # Default model
```

### Using Pre-captured Test Output

If you already have test output, you can skip running tests:

```bash
# Capture test output first
pytest tests/ -v --tb=short > test_failures.log 2>&1

# Run debugger on the output
export LLM_API_KEY="your-api-key"
export TEST_OUTPUT_FILE="test_failures.log"
uv run python examples/03_github_workflows/04_sre_debugger/agent_script.py
```

### Verification Checklist

**ERROR_ANALYSIS.md created** - File exists in repository root

**Executive summary** - Shows total failures and affected components

**Detailed analysis** - Each failure has:
- Location and error type
- Full error message and stack trace
- Root cause explanation
- Affected code snippets
- Suggested fixes with code examples
- Priority level

**Actionable recommendations** - Specific steps to fix issues

## Configuration

### Test Path Options

- **Default**: `tests/` (all tests)
- **Specific directory**: `tests/sdk/` (faster, focused)
- **Single file**: `tests/sdk/test_agent.py`
- **Specific test**: `tests/sdk/test_agent.py::test_function`

### LLM Model Options

```bash
export LLM_MODEL="gpt-4"
export LLM_MODEL="claude-opus-4-20250514"
export LLM_MODEL="openhands/gpt-4o"
```

### Test Output Handling

The script automatically:
- Truncates output if > 5000 characters (keeps last 5000)
- Exits gracefully if no failures detected
- Handles pytest timeouts (5 minute limit)

## Example Use Cases

1. **CI/CD Integration**
   - Auto-debug test failures in pull requests
   - Generate analysis reports for failing builds
   - Track recurring error patterns

2. **Local Development**
   - Quick debugging of test failures
   - Understanding complex error traces
   - Getting fix suggestions before committing

3. **Production Monitoring**
   - Adapt to analyze application logs
   - Debug production errors with context
   - Identify root causes from stack traces

4. **Code Review**
   - Automated analysis of test failures
   - Pre-review error investigation
   - Documentation of known issues

## Output Example

See the generated ERROR_ANALYSIS.md in this repository for a real example of the agent's debugging output.

## Troubleshooting

**Issue**: "No test failures detected"
- **Solution**: Tests are passing! If you expect failures, check the test path.

**Issue**: "Test execution timed out"
- **Solution**: Reduce TEST_PATH to a smaller subset of tests or increase timeout in agent_script.py.

**Issue**: Agent doesn't find root cause
- **Solution**: Ensure the failing tests and source code are accessible. Check test output is complete.

**Issue**: ERROR_ANALYSIS.md not created
- **Solution**: Check logs for errors. Ensure LLM_API_KEY is valid and tests actually failed.

## References

- [OpenHands SDK Documentation](https://docs.all-hands.dev/)
- [Pytest Documentation](https://docs.pytest.org/)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [LLM Provider Setup](https://docs.all-hands.dev/openhands/usage/llms/openhands-llms)
