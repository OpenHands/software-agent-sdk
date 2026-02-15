"""
Prompt templates for multi-model PR review debate.

This module contains prompt templates for:
- Initial PR review
- Debate consolidation
- Debate discussion
"""

from __future__ import annotations


# Section for review context
_REVIEW_CONTEXT_SECTION = """
## Previous Review History

The following shows previous reviews and review threads on this PR. Pay attention to:
- **Unresolved threads**: These issues may still need to be addressed
- **Resolved threads**: These provide context on what was already discussed
- **Previous review decisions**: See what other reviewers have said

{review_context}

When reviewing, consider:
1. Don't repeat comments that have already been made and are still relevant
2. If an issue is still unresolved in the code, you may reference it
3. If resolved, don't bring it up unless the fix introduced new problems
4. Focus on NEW issues in the current diff that haven't been discussed yet
"""

# Initial review prompt template
INITIAL_REVIEW_PROMPT = """{skill_trigger}

Review the PR changes below and identify issues that need to be addressed.

## Pull Request Information
- **Title**: {title}
- **Description**: {body}
- **Repository**: {repo_name}
- **Base Branch**: {base_branch}
- **Head Branch**: {head_branch}
- **PR Number**: {pr_number}
- **Commit ID**: {commit_id}
{review_context_section}
## Git Diff

```diff
{diff}
```

Provide a structured code review with:
1. **Overall Assessment**: High-level evaluation of the changes
2. **Critical Issues**: Bugs, security concerns, or breaking changes (if any)
3. **Code Quality**: Style, readability, and best practices feedback
4. **Suggestions**: Improvements that would make the code better

Be constructive and specific. Reference file names and line numbers when possible.
"""

# Consolidated reviews prompt for debate
DEBATE_INITIAL_PROMPT = """## Multi-Model Code Review Consolidation

Three different AI models have reviewed this PR. Here are their reviews:

{reviews}

## Your Task

You are participating in a code review debate with the other models.
Review the consolidated feedback above and:

1. **Identify agreements**: Points where multiple reviewers agree
2. **Identify disagreements**: Points where reviewers differ
3. **State your position**: On any disagreements, explain your reasoning

You have access to tools to communicate with the other reviewers:
- Use `send_to_reviewer` to share your thoughts or ask questions
- Each reviewer can respond to build consensus

The goal is to produce a high-quality, well-reasoned final review.
Focus on the most impactful issues and reach consensus where possible.
"""

# Debate round prompt
DEBATE_ROUND_PROMPT = """## Debate Round {round_number}

The discussion so far:
{discussion_history}

Continue the debate by:
1. Responding to points raised by other reviewers
2. Clarifying your position if misunderstood
3. Acknowledging valid points from others
4. Working toward consensus on key issues

Use the `send_to_reviewer` tool to communicate with specific reviewers.
If you believe consensus has been reached, summarize the agreed-upon points.
"""

# Final consolidation prompt
FINAL_CONSOLIDATION_PROMPT = """## Final Review Consolidation

After debating with other reviewers, produce the final consolidated review.

The debate history:
{debate_history}

Create a final review that:
1. Incorporates agreed-upon feedback from all reviewers
2. Notes any remaining disagreements with brief reasoning
3. Prioritizes issues by severity
4. Provides actionable suggestions

Format the review as:

## Overall Assessment
[High-level summary]

## Critical Issues (if any)
[Issues that must be addressed before merge]

## Suggestions
[Improvements ordered by importance]

## Notes
[Any additional context or minority opinions from the debate]
"""


def format_initial_review_prompt(
    skill_trigger: str,
    title: str,
    body: str,
    repo_name: str,
    base_branch: str,
    head_branch: str,
    pr_number: str,
    commit_id: str,
    diff: str,
    review_context: str = "",
) -> str:
    """Format the initial review prompt.

    Args:
        skill_trigger: Skill trigger (e.g., '/codereview')
        title: PR title
        body: PR description
        repo_name: Repository name (owner/repo)
        base_branch: Base branch name
        head_branch: Head branch name
        pr_number: PR number
        commit_id: HEAD commit SHA
        diff: Git diff content
        review_context: Previous review context

    Returns:
        Formatted prompt string
    """
    if review_context and review_context.strip():
        review_context_section = _REVIEW_CONTEXT_SECTION.format(
            review_context=review_context
        )
    else:
        review_context_section = ""

    return INITIAL_REVIEW_PROMPT.format(
        skill_trigger=skill_trigger,
        title=title,
        body=body,
        repo_name=repo_name,
        base_branch=base_branch,
        head_branch=head_branch,
        pr_number=pr_number,
        commit_id=commit_id,
        review_context_section=review_context_section,
        diff=diff,
    )


def format_debate_initial_prompt(reviews: dict[str, str]) -> str:
    """Format the debate initialization prompt.

    Args:
        reviews: Dictionary mapping model names to their reviews

    Returns:
        Formatted prompt string
    """
    reviews_text = ""
    for model_name, review in reviews.items():
        reviews_text += f"### Review from {model_name}\n\n{review}\n\n---\n\n"

    return DEBATE_INITIAL_PROMPT.format(reviews=reviews_text)


def format_debate_round_prompt(
    round_number: int,
    discussion_history: str,
) -> str:
    """Format a debate round prompt.

    Args:
        round_number: Current round number
        discussion_history: History of discussion so far

    Returns:
        Formatted prompt string
    """
    return DEBATE_ROUND_PROMPT.format(
        round_number=round_number,
        discussion_history=discussion_history,
    )


def format_final_consolidation_prompt(debate_history: str) -> str:
    """Format the final consolidation prompt.

    Args:
        debate_history: Full debate history

    Returns:
        Formatted prompt string
    """
    return FINAL_CONSOLIDATION_PROMPT.format(debate_history=debate_history)
