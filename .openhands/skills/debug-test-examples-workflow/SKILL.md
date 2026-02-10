---
name: debug-test-examples-workflow
description: Guide for debugging failing example tests in the `test-examples` labeled workflow. Use this skill when investigating CI failures in the run-examples.yml workflow, when example scripts fail to run correctly, when needing to isolate specific test failures, or when analyzing workflow logs and failure patterns.
---

# Debugging test-examples Workflow

## Overview

The `run-examples.yml` workflow runs example scripts from `examples/` directory. Triggers:
- Adding `test-examples` label to a PR
- Manual workflow dispatch
- Scheduled nightly runs

## Debugging Steps

### 1. Isolate Failing Tests

Modify `tests/examples/test_examples.py` to focus on specific tests:

```python
_TARGET_DIRECTORIES = (
    # EXAMPLES_ROOT / "01_standalone_sdk",
    EXAMPLES_ROOT / "02_remote_agent_server",  # Keep only failing directory
)
```

### 2. Exclude Tests

Add to `_EXCLUDED_EXAMPLES` with explanation:

```python
_EXCLUDED_EXAMPLES = {
    # Reason for exclusion
    "examples/path/to/failing_test.py",
}
```

### 3. Trigger Workflow

Toggle the `test-examples` label:

```bash
# Remove label
curl -X DELETE -H "Authorization: token $GITHUB_TOKEN" \
  "https://api.github.com/repos/OpenHands/software-agent-sdk/issues/${PR_NUMBER}/labels/test-examples"

# Add label
curl -X POST -H "Authorization: token $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  "https://api.github.com/repos/OpenHands/software-agent-sdk/issues/{PR_NUMBER}/labels" \
  -d '{"labels":["test-examples"]}'
```

### 4. Monitor Progress

```bash
# Check status
curl -s -H "Authorization: token $GITHUB_TOKEN" \
  "https://api.github.com/repos/OpenHands/software-agent-sdk/actions/runs/{RUN_ID}" | jq '{status, conclusion}'

# Download logs
curl -sL -H "Authorization: token $GITHUB_TOKEN" \
  "https://api.github.com/repos/OpenHands/software-agent-sdk/actions/runs/{RUN_ID}/logs" -o logs.zip
unzip logs.zip -d logs
```

## Common Failure Patterns

| Pattern | Cause | Solution |
|---------|-------|----------|
| Port conflicts | Fixed ports (8010, 8011) | Run with `-n 1` or use different ports |
| Container issues | Docker/Apptainer setup | Check Docker availability, image pulls |
| LLM failures | Transient API errors | Retry the test |
| Example bugs | Code errors | Check traceback |


## Key Configuration

**Workflow** (`.github/workflows/run-examples.yml`):
- Runner: `blacksmith-2vcpu-ubuntu-2404`
- Timeout: 60 minutes
- Parallelism: `-n 4` (pytest-xdist: 4 parallel workers)

**Tests** (`tests/examples/test_examples.py`):
- Timeout per example: 600 seconds
- Target directories: `_TARGET_DIRECTORIES`
- Excluded examples: `_EXCLUDED_EXAMPLES`
