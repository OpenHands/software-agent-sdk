"""
PR Review Prompt Template

This module contains the prompt template used by the OpenHands agent
for conducting pull request reviews.

The template uses skill triggers:
- {skill_trigger} will be replaced with '/codereview' or '/codereview-roasted'
- /github-pr-review provides instructions for posting review comments via GitHub API (from public skills)

The template includes:
- {diff} - The complete git diff for the PR (may be truncated for large files)
- {pr_number} - The PR number
- {commit_id} - The HEAD commit SHA
"""

PROMPT = """{skill_trigger}
/github-pr-review

Review the PR changes below and identify issues that need to be addressed.

## Pull Request Information
- **Title**: {title}
- **Description**: {body}
- **Repository**: {repo_name}
- **Base Branch**: {base_branch}
- **Head Branch**: {head_branch}
- **PR Number**: {pr_number}
- **Commit ID**: {commit_id}

## Git Diff

```diff
{diff}
```

Analyze the changes and post your review using the GitHub API.
"""
