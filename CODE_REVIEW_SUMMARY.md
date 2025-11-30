# Code Review Microagent - Summary

## What Was Done

### 1. Data Collection ✅
- Used GitHub API to fetch code review history from OpenHands/software-agent-sdk
- Analyzed **153 review comments** across **37 pull requests**
- Extracted both line-level comments and PR-level reviews
- Focused on recent PRs (last 100) to capture current review patterns

### 2. Pattern Analysis ✅
Identified key review principles through automated analysis:

**Top Themes:**
- **AI Delegation (24 instances)**: Frequent use of @OpenHands for implementation
- **Quick Approvals (19 instances)**: "LGTM!" for good code
- **Testing Focus (16 instances)**: Pragmatic test coverage
- **Code Suggestions (13 instances)**: Specific, actionable feedback
- **Simplicity Focus (5+ instances)**: Questioning over-complexity

### 3. Key Principles Extracted ✅

1. **Simplicity First** - "This feels a bit overcomplicated to me -- what's the use case?"
2. **Pragmatic Testing** - Avoid duplicate tests, test real scenarios
3. **Type Safety Without Compromise** - Fix types properly, avoid `# type: ignore`
4. **AI-Assisted Development** - Leverage @OpenHands for implementation tasks
5. **Backward Compatibility** - Evaluate breaking change impact carefully
6. **Real Use Cases** - Always validate necessity and actual problems
7. **Concise Communication** - Casual, direct, with emojis (👀 🤣 🤕)

### 4. Microagent File Created ✅

**Location**: `.openhands/microagents/code-review.md`

**Trigger**: `/codereview`

**Structure** (following OpenHands/OpenHands reference):
- Frontmatter with trigger
- PERSONA section
- CORE PHILOSOPHY (5 key principles)
- REVIEW FRAMEWORK (4 key questions)
- CODE REVIEW SCENARIOS (7 scenarios with examples)
- REVIEW OUTPUT FORMAT (templates)
- COMMUNICATION STYLE
- SPECIFIC REPOSITORY PATTERNS
- EXAMPLE REVIEWS (6 real examples)

### 5. Documentation Created ✅

Three files created for reference:

1. **`.openhands/microagents/code-review.md`** - The microagent itself
2. **`code_review_analysis.md`** - Detailed analysis of patterns and principles
3. **`code_review_history.txt`** - Raw review data (4544 lines, 153 reviews)

## How to Use

### Activate the Code Review Microagent

In any pull request or when reviewing code, use:
```
/codereview
```

This will activate the code review persona based on the analyzed patterns.

### Alternative: Roasted Review

For a more critical, Linus-style review:
```
/codereview-roasted
```

## Key Differences from "Roasted" Style

| Aspect | Regular Review | Roasted Review |
|--------|---------------|----------------|
| Tone | Casual, collaborative | Critical, direct |
| Verbosity | Concise, often one-liners | Detailed analysis |
| AI Usage | Heavy delegation to @OpenHands | Review-only, no delegation |
| Harshness | Constructive | Brutally honest |
| Focus | Practical simplicity | Engineering fundamentals |

## Examples from Real Reviews

### Questioning Complexity
```
This feels a bit overcomplicated to me -- what's the use case for this 👀

Maybe we can ship the file directly with the SDK so we don't need to make 
this request but rather read local file -- WDYT?
```

### Pragmatic Testing
```
This test doesn't make a lot sense to me - the other file already tests 
all the scenarios
```

### AI Delegation
```
opps i forgot this before merging this PR 🤕

@OpenHands please implement this and push to a separate PR
```

### Type Safety
```
Please AVOID using # type: ignore[attr-defined]. If this can be fixed 
with a few assert statements, do that instead.
```

### Quick Approval
```
LGTM!
```
```
confirmed working!
```

## Statistics

- **Total PRs Analyzed**: 100 recent PRs
- **PRs with Reviews**: 37
- **Total Review Items**: 153
- **Most Common Theme**: AI Delegation (24 instances)
- **Approval Rate**: 19 quick approvals observed

## Files in This Repository

```
.openhands/microagents/code-review.md  # The microagent (7.6K)
code_review_analysis.md                # Detailed analysis (189 lines)
code_review_history.txt                # Raw review data (4544 lines)
CODE_REVIEW_SUMMARY.md                 # This file
```

## Next Steps

The microagent is ready to use! Simply trigger it with `/codereview` in any code review context.

For future improvements, you can:
1. Refine the microagent based on feedback
2. Add more specific patterns as they emerge
3. Update with new repository conventions
4. Create additional specialized review agents (e.g., `/codereview-security`, `/codereview-performance`)
