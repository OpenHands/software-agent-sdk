# Agent Behavior Testing Framework

This document describes the behavior testing framework integrated into the existing integration test suite.

## Overview

**Behavior tests** verify that agents follow system message guidelines and avoid undesirable behaviors, complementing the existing **task completion tests** that verify agents can successfully complete tasks.

Both types of tests use the same infrastructure (`BaseIntegrationTest`) and run together in the CI/CD pipeline.

## Test Types

| Type | Focus | Criticality | Example |
|------|-------|-------------|---------|
| **Task Completion** | Agent successfully completes tasks | `critical` | `t01_fix_simple_typo.py` - fixes typos in a file |
| **Behavior** | Agent follows system guidelines | `ux` | `t09_no_premature_implementation.py` - doesn't implement when asked for advice |

### Test Criticality Levels

Tests are classified by criticality to distinguish between core functionality and UX improvements:

- **`critical`**: Core functionality tests that must pass. These verify that the agent can successfully complete essential tasks. Failures in critical tests block releases.
- **`ux`**: User experience tests that track quality improvements. These verify that the agent follows best practices and avoids annoying behaviors. Failures in UX tests don't block releases but should be addressed for optimal user experience.

## Behavior Tests

### What They Test

Behavior tests verify that agents:
- ✅ Don't create unnecessary documentation files
- ✅ Wait for confirmation before implementing
- ✅ Use specialized tools instead of bash commands
- ✅ Don't start implementing when asked for advice
- ✅ Follow other system message guidelines

### Current Behavior Tests

1. **t09_no_premature_implementation.py**
   - Tests: Agent doesn't start implementing when asked for advice
   - Prompt: Asks "how to implement" a feature
   - Expected: Agent explores, suggests approaches, asks questions
   - Failure: Agent creates/edits files without being asked
   - Uses: LLM-as-judge for behavior quality assessment

2. **t10_no_unnecessary_markdown.py**
   - Tests: Agent doesn't create markdown files unnecessarily
   - Prompt: Asks to understand existing code
   - Expected: Agent reads code and explains in conversation
   - Failure: Agent creates README.md, DOCS.md, etc.

3. **t11_use_specialized_tools.py**
   - Tests: Agent uses FileEditorTool instead of bash commands
   - Prompt: Asks to read a file
   - Expected: Agent uses FileEditorTool view command
   - Failure: Agent uses `cat`, `head`, `tail` via bash

## Writing Behavior Tests

### 1. Create Test File

Create a file in `tests/integration/tests/` with naming pattern `t##_*.py`:

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
    CRITICALITY: str = "ux"  # Use "ux" for behavior tests, "critical" for task completion

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

**Note**: Set `CRITICALITY = "ux"` for behavior tests and `CRITICALITY = "critical"` for task completion tests. If not specified, tests default to `"critical"`.

### 2. Use Helper Methods

`BaseIntegrationTest` provides behavior checking helpers:

```python
# Find tool calls
file_editor_calls = self.find_tool_calls("FileEditorTool")
terminal_calls = self.find_tool_calls("TerminalTool")

# Find file editing operations (create, str_replace, insert, undo_edit)
edits = self.find_file_editing_operations()

# Find specific file operations
markdown_ops = self.find_file_operations(file_pattern="*.md")

# Check bash command usage
cat_commands = self.check_bash_command_used("cat")

# Get conversation summary for LLM judge
summary = self.get_conversation_summary()
```

### 3. Use LLM-as-Judge (Optional)

For complex behavior verification:

```python
from tests.integration.utils.llm_judge import judge_agent_behavior

judgment = judge_agent_behavior(
    user_instruction=INSTRUCTION,
    conversation_summary=self.get_conversation_summary(),
    evaluation_criteria="""
    The agent should:
    1. Do X (GOOD)
    2. Not do Y (BAD)

    Did the agent behave appropriately?
    """,
    llm=self.llm  # Reuse test LLM instance
)

if not judgment.approved:
    return TestResult(
        success=False,
        reason=f"LLM judge rejected: {judgment.reasoning}"
    )
```

## Running Tests

### Run All Tests (Including Behavior Tests)

```bash
# Run all integration tests locally
python tests/integration/run_infer.py \
  --llm-config '{"model": "claude-sonnet-4-5-20250929"}' \
  --num-workers 4 \
  --eval-note "local-test"

# Run specific tests only
python tests/integration/run_infer.py \
  --llm-config '{"model": "claude-sonnet-4-5-20250929"}' \
  --eval-ids "t09_no_premature_implementation,t10_no_unnecessary_markdown"
```

### Run in CI/CD

Behavior tests run automatically alongside task completion tests when:
- The `integration-test` label is added to a PR
- Workflow is triggered manually
- Nightly scheduled run

The existing `.github/workflows/integration-runner.yml` workflow handles both test types.

## Test Results

Results include both task completion and behavior tests:

```
Success rate: 90.91% (10/11)
✓: t01_fix_simple_typo - Successfully fixed all typos
✓: t09_no_premature_implementation - Agent correctly provided advice without implementing
✗: t10_no_unnecessary_markdown - Agent created README.md without being asked
...
```

## Helper Methods Reference

### `find_tool_calls(tool_name: str) -> list[Event]`
Find all calls to a specific tool.

**Example:**
```python
file_editor_calls = self.find_tool_calls("FileEditorTool")
```

### `find_file_editing_operations() -> list[Event]`
Find all file editing operations (excludes read-only `view` operations).

**Returns:** Events for `create`, `str_replace`, `insert`, `undo_edit` commands.

### `find_file_operations(file_pattern: str | None) -> list[Event]`
Find all file operations, optionally filtered by pattern.

**Example:**
```python
markdown_ops = self.find_file_operations("*.md")
python_ops = self.find_file_operations("*.py")
```

### `check_bash_command_used(command_pattern: str) -> list[Event]`
Check if agent used bash commands matching a pattern.

**Example:**
```python
cat_usage = self.check_bash_command_used("cat")
grep_usage = self.check_bash_command_used("grep")
```

### `get_conversation_summary(max_length: int = 5000) -> str`
Get a text summary of the conversation for LLM judge.

## Adding New Behavior Tests

1. **Identify undesirable behavior** from real agent failures
2. **Create a prompt** that might trigger that behavior
3. **Write test** using the pattern above
4. **Verify locally** before committing
5. **Document** what behavior you're testing and why

## System Message Optimization

Behavior tests serve as **regression tests for system messages**. When evolving system messages:

1. Run behavior test suite
2. Identify tests that start failing
3. Analyze if the failure indicates:
   - System message needs improvement
   - Test needs updating
   - Acceptable trade-off
4. Iterate on system message
5. Re-run tests to verify

## Best Practices

- **Be specific:** Test one behavior per test
- **Use realistic prompts:** Base tests on real failure cases
- **Check early:** Behavior tests fail fast if agent starts wrong actions
- **Use LLM judge wisely:** For nuanced behavior, not simple checks
- **Document intent:** Explain WHY a behavior is undesirable

## Future Enhancements

Potential improvements to the framework:

- [ ] Test categories/tags for filtering
- [ ] Behavior severity levels (warning vs error)
- [ ] More sophisticated LLM judge prompts
- [ ] Automatic prompt variation generation
- [ ] Historical tracking of behavior test pass rates
- [ ] A/B testing system messages with behavior tests

## Questions?

See existing tests for examples:
- `tests/integration/tests/t09_no_premature_implementation.py`
- `tests/integration/tests/t10_no_unnecessary_markdown.py`
- `tests/integration/tests/t11_use_specialized_tools.py`
