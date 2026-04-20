---
name: write-behavior-test
description: Guide for writing behavior tests that verify agents follow system message guidelines and avoid undesirable behaviors. Use when creating integration tests for agent behavior validation.
triggers:
- /write_behavior_test
---

# Behavior Test Writing Guide

You are helping to create **behavior tests** for the agent-sdk integration test suite. These tests verify that agents follow system message guidelines and avoid undesirable behaviors.

The tests are for the agent powered by this SDK, so you may need to refer the codebase for details on how the agent works in order to write effective tests.

## Behavior Tests vs Task Tests

**Task Tests (t*.py)** - REQUIRED tests that verify task completion:
- Focus: Can the agent successfully complete the task?
- Example: Fix typos in a file, create a script, implement a feature

**Behavior Tests (b*.py)** - OPTIONAL tests that verify proper behavior:
- Focus: Does the agent follow best practices and system guidelines?
- Example: Don't implement when asked for advice, don't over-verify, avoid redundant files

## Key Principles for Writing Behavior Tests

### ✅ DO:

1. **Use Real Repositories**
   - Clone actual GitHub repositories that represent real-world scenarios
   - Pin to a specific historical commit (before a fix/feature was added)
   - Example: `clone_pinned_software_agent_repo(workspace)` helper

2. **Test Realistic Complex, Nuanced Behaviors**
   - Try to make the task as realistic as possible to real HUMAN interactions, from file naming, (somewhat lazy) instruction style, etc
   - Focus on subtle behavioral issues that require judgment
   - Test scenarios where the "right" behavior isn't immediately obvious
   - Examples: When to implement vs advise, when to stop testing, whether to add backward compatibility

3. **Clean Up Repository History**
   - Check out to a commit BEFORE the solution exists
   - Reset/remove future commits (see existing tests for examples)
   - Ensures the agent experiences the same context as real users

4. **Use Helper Functions**
   - `find_file_editing_operations(events)` - Find file create/edit operations
   - `find_tool_calls(events, tool_name)` - Find specific tool usage
   - `get_conversation_summary(events)` - Get summary for LLM judge
   - `judge_agent_behavior(...)` - Use LLM to evaluate behavior quality

5. **Leverage LLM Judges**
   - Use `judge_agent_behavior()` for subjective evaluations
   - Provide clear evaluation criteria in the judge prompt
   - Track judge usage costs: `self.add_judge_usage(prompt_tokens, completion_tokens, cost)`

6. **Adaptation of Problem Description to Task**
   - If you find the problem description is not easy to adapt to a behavior test, e.g. it requires complex environment setup like kubernetes, try to come up with a simpler problem description that still captures the essence of the behavior you want to test but is easier to implement in the test framework.
   - Ensure the instructions naturally lead to the behavior you want to evaluate

### ❌ DO NOT:

1. **Avoid Simple Synthetic Tests**
   - Don't create artificial scenarios with minimal setup
   - Don't test behaviors that are too obvious or straightforward
   - Example: Don't create a single-file test with trivial content

2. **Don't Test Basic Functionality**
   - Behavior tests are NOT for testing if the agent can use tools
   - Task tests handle basic capability verification
   - Focus on HOW the agent approaches problems, not IF it can solve them

3. **Don't Overcomplicate Static Assertions**
   - Use assertions for clear-cut checks (e.g., no file edits)
   - Rely on LLM judges for nuanced behavior evaluations
   - Avoid trying to encode subjective judgments purely in code or too much static logic

## Tips for Test Difficulty Calibration

**Make tests challenging but not impossible and too long:**

1. **Context Complexity**: Use real codebases with multiple files and dependencies, either the software-agent-sdk or other popular open-source repos you find suitable
2. **Ambiguity**: Prefer instructions that could be interpreted multiple ways
3. **Temptation**: Set up scenarios where the "easy wrong path" is tempting
4. **Realism**: Mirror real user interactions and expectations

**Examples of Good Complexity:**
- "How to implement X?" (tests if agent implements vs advises)
- "Update constant Y" (tests if agent over-verifies with excessive test runs)
- "Rename method A to B" (tests if agent adds unnecessary backward compatibility)

## Example Behavior Test Patterns

1. **Premature Implementation** - Tests if agent implements when asked for advice only
2. **Over-verification** - Tests if agent runs excessive tests beyond what's needed
3. **Unnecessary Compatibility** - Tests if agent adds backward compatibility shims when not needed
4. **Redundant Artifacts** - Tests if agent creates extra files (docs, READMEs) without being asked
5. **Communication Quality** - Tests if agent provides explanations for actions

## File Naming Convention

Name your test file: `b##_descriptive_name.py`
- `b` prefix indicates behavior test (auto-detected)
- `##` is a zero-padded number (e.g., 01, 02, 03)
- Use snake_case for the descriptive name

## Final Checklist

Before submitting your behavior test, verify:

- [ ] Uses a real repository or complex codebase
- [ ] Tests a nuanced behavior, not basic functionality
- [ ] Includes clear and not overly complex verification logic (assertions or LLM judge)
- [ ] Has a descriptive docstring explaining what behavior is tested
- [ ] Properly tracks judge usage costs if using LLM evaluation
- [ ] Follows naming convention: `b##_descriptive_name.py`
- [ ] Test is realistic and based on actual behavioral issues observed

Remember: The goal is to catch subtle behavioral issues that would appear in real-world usage, serving as regression tests for system message improvements.
