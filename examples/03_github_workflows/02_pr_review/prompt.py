"""
PR Review Prompt Template

This module contains the prompt template used by the OpenHands agent
for conducting pull request reviews.

The template supports skill triggers:
- {skill_trigger} will be replaced with either '/codereview' or '/codereview-roasted'
  to activate the appropriate code review skill from the public skills repository.
"""

PROMPT = """{skill_trigger}

Use bash commands to analyze the PR changes and identify issues that need to
be addressed.

## Pull Request Information
- **Title**: {title}
- **Description**: {body}
- **Repository**: {repo_name}
- **Base Branch**: {base_branch}
- **Head Branch**: {head_branch}

## Analysis Process
Use bash commands to understand the changes, check out diffs and examine
the code related to the PR.

Start by analyzing the changes with bash commands, then provide your structured review.
"""
