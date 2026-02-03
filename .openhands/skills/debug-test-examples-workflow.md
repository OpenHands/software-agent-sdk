# Debugging test-examples Workflow

This document describes the methodology for debugging failing example tests in the `run-examples.yml` workflow.

## Overview

The `run-examples.yml` workflow runs example scripts from the `examples/` directory to verify they work correctly. Tests are triggered by:
- Adding the `test-examples` label to a PR
- Manual workflow dispatch
- Scheduled nightly runs

## Debugging Methodology

### 1. Isolate the Failing Tests

When debugging specific test failures, modify `tests/examples/test_examples.py` to focus on the failing tests:

```python
# Comment out other directories to focus on specific tests
_TARGET_DIRECTORIES = (
    # EXAMPLES_ROOT / "01_standalone_sdk",
    EXAMPLES_ROOT / "02_remote_agent_server",  # Keep only the failing directory
    # EXAMPLES_ROOT / "05_skills_and_plugins" / "01_loading_agentskills",
)
```

### 2. Add Failing Tests to Exclusion List

If tests need to be excluded temporarily or permanently, add them to `_EXCLUDED_EXAMPLES`:

```python
_EXCLUDED_EXAMPLES = {
    # ... existing exclusions ...
    # Add comment explaining why the test is excluded
    "examples/path/to/failing_test.py",
}
```

### 3. Trigger the Workflow

To trigger the workflow on a PR:
1. Remove the `test-examples` label
2. Re-add the `test-examples` label

```bash
# Remove label
curl -X DELETE -H "Authorization: token $GITHUB_TOKEN" \
  "https://api.github.com/repos/OpenHands/software-agent-sdk/issues/{PR_NUMBER}/labels/test-examples"

# Add label
curl -X POST -H "Authorization: token $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  "https://api.github.com/repos/OpenHands/software-agent-sdk/issues/{PR_NUMBER}/labels" \
  -d '{"labels":["test-examples"]}'
```

### 4. Monitor Workflow Progress

```bash
# Check workflow status
curl -s -H "Authorization: token $GITHUB_TOKEN" \
  "https://api.github.com/repos/OpenHands/software-agent-sdk/actions/runs/{RUN_ID}" | jq '{status, conclusion}'

# Download and inspect logs
curl -sL -H "Authorization: token $GITHUB_TOKEN" \
  "https://api.github.com/repos/OpenHands/software-agent-sdk/actions/runs/{RUN_ID}/logs" -o logs.zip
unzip logs.zip -d logs
cat logs/test-examples/10_Run\ examples.txt | tail -100
```

### 5. Analyze Failure Patterns

Common failure patterns:

1. **Port conflicts**: Tests using fixed ports (8010, 8011) cannot run in parallel
   - Solution: Run tests sequentially with `-n 1` or use different ports

2. **Container issues**: Docker/Apptainer tests may fail due to:
   - Missing Docker setup in CI
   - tmux initialization issues inside containers
   - Image pull failures

3. **LLM-related failures**: Transient failures from LLM API
   - These are usually flaky and may pass on retry

4. **Example code bugs**: Errors in the example code itself
   - Check the traceback for the specific error

## Known Issues

### Docker/Apptainer Sandboxed Server Tests

The following tests are excluded due to tmux initialization issues inside containers:
- `02_convo_with_docker_sandboxed_server.py`
- `03_browser_use_with_docker_sandboxed_server.py`
- `04_convo_with_api_sandboxed_server.py`
- `05_vscode_with_docker_sandboxed_server.py`
- `08_convo_with_apptainer_sandboxed_server.py`

Root cause: The TmuxTerminal fails with "Could not find object" when trying to create a new tmux session via libtmux inside Docker/Apptainer containers.

## Workflow Configuration

Key workflow settings in `.github/workflows/run-examples.yml`:

- **Runner**: `blacksmith-2vcpu-ubuntu-2404`
- **Timeout**: 60 minutes
- **Parallelism**: `-n 4` (4 parallel workers)
- **Docker**: Available on the runner, login to GHCR for image pulls
- **Apptainer**: Set up via `eWaterCycle/setup-apptainer@v2`

## Test Configuration

Key settings in `tests/examples/test_examples.py`:

- **Timeout per example**: 600 seconds (10 minutes)
- **Target directories**: Defined in `_TARGET_DIRECTORIES`
- **Excluded examples**: Defined in `_EXCLUDED_EXAMPLES`
- **LLM-specific examples**: Defined in `_LLM_SPECIFIC_EXAMPLES` with model overrides
