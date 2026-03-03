---
name: eval-risk-assessment
description: Guidelines for identifying and flagging changes that could affect benchmark/evaluation performance.
---

# Eval Risk Assessment

When reviewing or submitting PRs, flag changes that could plausibly affect benchmark/evaluation performance for human review and lightweight evals.

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
