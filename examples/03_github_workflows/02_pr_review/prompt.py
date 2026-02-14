"""
PR Review Prompt Template

This module contains the prompt template used by the OpenHands agent
for conducting pull request reviews.

The template uses skill triggers:
- {skill_trigger} will be replaced with '/codereview' or '/codereview-roasted'
- /github-pr-review provides instructions for posting review comments via GitHub API

The template includes:
- {diff} - The complete git diff for the PR (may be truncated for large files)
- {pr_number} - The PR number
- {commit_id} - The HEAD commit SHA
- {review_context} - Previous review comments and thread resolution status
"""

# Template for when there is review context available
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

PROMPT = """{skill_trigger}
/github-pr-review

When posting a review, keep the review body brief unless your active review instructions
require a longer structured format.

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

Analyze the changes and post your review using the GitHub API.
"""


def format_prompt(
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
    """Format the PR review prompt with all parameters.

    Args:
        skill_trigger: The skill trigger (e.g., '/codereview' or '/codereview-roasted')
        title: PR title
        body: PR description
        repo_name: Repository name (owner/repo)
        base_branch: Base branch name
        head_branch: Head branch name
        pr_number: PR number
        commit_id: HEAD commit SHA
        diff: Git diff content
        review_context: Formatted previous review context. If empty or whitespace-only,
            the review context section is omitted from the prompt.

    Returns:
        Formatted prompt string
    """
    # Only include the review context section if there is actual context
    if review_context and review_context.strip():
        review_context_section = _REVIEW_CONTEXT_SECTION.format(
            review_context=review_context
        )
    else:
        review_context_section = ""

    return PROMPT.format(
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
