# Agent Behavior Testing Framework

This document describes the behavior testing framework integrated into the existing integration test suite.

## Overview

**Behavior tests** verify that agents follow system message guidelines and avoid undesirable behaviors, complementing the existing **task completion tests** that verify agents can successfully complete tasks.

Both types of tests use the same infrastructure (`BaseIntegrationTest`) and run together in the CI/CD pipeline.

## Test Types

| Type | Status | Focus | Example |
|------|--------|-------|---------|
| **Integration** (t*.py) | **Required** | Agent successfully completes tasks | `t01_fix_simple_typo.py` - fixes typos in a file |
| **Behavior** (b*.py) | **Optional** | Agent follows system guidelines | `b01_no_premature_implementation.py` - doesn't implement when asked for advice |

### Test Type Classification

Tests are classified by type to distinguish between required and optional tests:

- **Integration tests** (t*.py) - **REQUIRED**: Verify that the agent can successfully complete essential tasks. These tests must pass for releases and focus on whether the agent achieves the desired outcome.
- **Behavior tests** (b*.py) - **OPTIONAL**: Verify that the agent follows system message guidelines and best practices. These tests track quality improvements and don't block releases. They focus on how the agent approaches problems and interacts with users.

## Behavior Tests

### What They Test

Behavior tests verify that agents:
- ✅ Don't start implementing when asked for advice
- ✅ Follow system message guidelines and best practices
- ✅ Handle complex, nuanced scenarios appropriately

### Guidelines for Adding Behavior Tests

Behavior tests should focus on **complex, real-world scenarios** that reveal subtle behavioral issues:

**DO:**
- Use real repositories from real problems encountered in production or development
- Check out to a specific historic commit before the problem was fixed
- Reset/remove all future commits so the agent cannot "cheat" by seeing the solution (see `b01_no_premature_implementation.py` for example)
- Test complex, nuanced agent behaviors that require judgment
- Use realistic, multi-file codebases with actual context
- Consider using LLM judges to evaluate behavior quality when appropriate

**DO NOT:**
- Add simple, synthetic tests that can be easily verified with basic assertions
- Create artificial scenarios with minimal setup (single file with trivial content)
- Test behaviors that are too obvious or straightforward
- Write tests where the "correct" behavior is immediately evident from the instruction

The goal is to catch subtle behavioral issues that would appear in real-world usage, not to test basic functionality.

## Writing Behavior Tests

### 1. Create Test File

Create a file in `tests/integration/tests/` with naming pattern `b##_*.py`:

```python
"""Test description here."""

import os
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool
from tests.integration.base import BaseIntegrationTest, TestResult

INSTRUCTION = "Your user prompt that might trigger undesirable behavior"

class YourBehaviorTest(BaseIntegrationTest):
    INSTRUCTION: str = INSTRUCTION
    # Note: Test type is automatically determined by filename (b*.py = behavior)

    @property
    def tools(self) -> list[Tool]:
        register_tool("TerminalTool", TerminalTool)
        register_tool("FileEditorTool", FileEditorTool)
        return [Tool(name="TerminalTool"), Tool(name="FileEditorTool")]

    def setup(self) -> None:
        # Create any files/directories needed for the test
        pass

    def verify_result(self) -> TestResult:
        # Check agent behavior using helper methods
        editing_ops = self.find_file_editing_operations()

        if editing_ops:
            return TestResult(
                success=False,
                reason="Agent edited files when it shouldn't have"
            )

        return TestResult(success=True, reason="Agent behaved correctly")
```

**Note**: Test type is automatically determined by the filename prefix:
- Files starting with `b` (e.g., `b01_*.py`) are classified as behavior tests
- Files starting with `t` (e.g., `t01_*.py`) are classified as integration tests

### 2. Validate Behavior

- Keep assertions focused on the user-facing behavior you want to enforce.
- Reach for `judge_agent_behavior` (see `tests/integration/utils/llm_judge.py`) when human-style evaluation is needed.
- Make setup faithful to real incidents so the agent experiences the same context users faced.

For additional patterns, read the existing suites such as `b01_no_premature_implementation.py`.

## Running Tests

Use the integration runner locally when developing new scenarios:

```bash
python tests/integration/run_infer.py \
  --llm-config '{"model": "claude-sonnet-4-5-20250929"}' \
  --eval-ids "b01_no_premature_implementation"
```

CI automatically runs behavior and integration tests together via `.github/workflows/integration-runner.yml` when the `integration-test` label is applied or the workflow is triggered manually.

## Test Results

Results include both integration and behavior tests with separate success rates:

```
Overall Success rate: 90.00% (9/10)
Integration tests (Required): 100.00% (8/8)
Behavior tests (Optional): 50.00% (1/2)
Evaluation Results:
✓: t01_fix_simple_typo - Successfully fixed all typos
✓: b01_no_premature_implementation - Agent correctly provided advice without implementing
...
```

In this example, all required integration tests passed (100%), while some optional behavior tests failed. This would not block a release, but the 
behavior test failures should be investigated for UX improvements.

## Adding New Behavior Tests

1. **Identify undesirable behavior** from real agent failures
2. **Create a prompt** that might trigger that behavior
3. **Write test** using the pattern above
4. **Verify locally** before committing
5. **Document** what behavior you're testing and why

## System Message Optimization

Behavior tests serve as **regression tests for system messages**. When evolving ystem messages:

1. Run behavior test suite
2. Identify tests that start failing
3. Analyze if the failure indicates:
   - System message needs improvement
   - Test needs updating
   - Acceptable trade-off
4. Iterate on system message
5. Re-run tests to verify