"""
PR Review Prompt Template

This module contains the prompt template used by the OpenHands agent
for conducting pull request reviews.

The template supports skill triggers:
- {skill_trigger} will be replaced with either '/codereview' or '/codereview-roasted'
  to activate the appropriate code review skill from the public skills repository.

The template includes:
- {diff} - The complete git diff for the PR (truncated for large files)
"""

PROMPT = """{skill_trigger}

Review the PR changes below and identify issues that need to be addressed.

## Pull Request Information
- **Title**: {title}
- **Description**: {body}
- **Repository**: {repo_name}
- **Base Branch**: {base_branch}
- **Head Branch**: {head_branch}

## Git Diff

The following is the complete diff for this PR. Large file diffs may be truncated.

```diff
{diff}
```

## Analysis Process

The diff above shows all the changes in this PR. You can use bash commands to examine
additional context if needed (e.g., to see the full file content or related code).

Analyze the changes and provide your structured review.
"""
