---
name: eval-risk-assessment
description: Guidelines for identifying and flagging changes that could affect benchmark/evaluation performance. Distilled from recurring code review feedback.
---

# Eval Risk Assessment

When reviewing or submitting PRs, flag changes that could plausibly affect benchmark/evaluation performance for human review and lightweight evals.

## Overview

This skill was distilled from recurring code review feedback in the repository. Multiple reviews flagged the need to assess "eval risk" before merging changes that affect agent behavior.

## Changes That Require Eval Verification

Flag the following types of changes:

1. **Prompt Template Modifications**
   - System prompts
   - Tool descriptions
   - Agent instructions

2. **Tool Behavior Changes**
   - Tool calling/execution logic
   - Tool output formatting
   - New or modified tool parameters

3. **Agent Decision Logic**
   - Loop/iteration control
   - Task delegation logic
   - State management changes

4. **LLM Integration Changes**
   - Model parameter handling (temperature, max_tokens, etc.)
   - Response parsing
   - Context management

## How to Flag Eval Risk

When submitting a PR with eval-risk changes:

```markdown
## Eval Risk Flag 🚩

This PR modifies [prompt templates / tool behavior / agent logic].
Recommend running lightweight evals before merge to verify no
unexpected impact on benchmark performance.
```

When reviewing a PR with eval-risk changes:

- Do NOT approve without human maintainer review
- Request lightweight eval verification
- Use `COMMENT` status instead of `APPROVE` if uncertain

## Examples from Code Reviews

> "According to the eval-risk policy... changes to prompt templates (which includes tool descriptions) should be flagged for human review with lightweight evals."

> "This PR touches git tool endpoints that agents use during coding tasks. Per review policy, flagging for lightweight eval verification before merge."

> "Eval Risk Flag: This changes max_output_tokens behavior... Should run lightweight evals before merge to confirm no unexpected impact on agent performance."

## Related Patterns

### Test New Behavior, Not Just Removed Behavior

When refactoring, tests should verify:
- The new behavior works as expected
- Not just that old behavior was removed

> "Test verifies what was removed but doesn't verify the NEW behavior."

### Document API Behavior Changes

When modifying public APIs:
- Update docstrings to reflect new behavior
- Note any breaking changes

> "The docstring doesn't clarify that it only returns user-registered agents, not built-in agents."
