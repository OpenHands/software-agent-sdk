"""
PR Review Prompt Template

This module contains the prompt template used by the OpenHands agent
for conducting pull request reviews.

The template supports skill triggers:
- {skill_trigger} will be replaced with either '/codereview' or '/codereview-roasted'
  to activate the appropriate code review skill from the public skills repository.

The template includes:
- {diff} - The complete git diff for the PR (truncated for large files)
- {pr_number} - The PR number
- {commit_id} - The HEAD commit SHA
"""

PROMPT = """{skill_trigger}

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

The following is the complete diff for this PR. Large file diffs may be truncated.

```diff
{diff}
```

## Analysis Process

The diff above shows all the changes in this PR. You can use bash commands to examine
additional context if needed (e.g., to see the full file content or related code).

Analyze the changes and identify specific issues that need attention.

## CRITICAL: Do NOT post to GitHub yourself

You MUST NOT call `gh api`, `curl https://api.github.com/...`, or any other network/API
command to post the review.

A separate script will post your review to GitHub and will automatically append
feedback links to every inline comment.

## Output Format (REQUIRED): JSON only

Your final answer MUST be a single JSON object (no surrounding markdown) with this schema:

{
  "event": "COMMENT" | "APPROVE" | "REQUEST_CHANGES",
  "summary": "1-3 sentence summary for the PR author.",
  "comments": [
    {
      "path": "path/in/repo/file.py",
      "side": "RIGHT" | "LEFT",
      "line": 123,
      "start_line": 120,               // optional (only for multi-line comments)
      "start_side": "RIGHT" | "LEFT", // optional
      "body": "🟠 Important: ... (include details and optional ```suggestion blocks```)"
    }
  ]
}

Rules:
- `comments` may be an empty list.
- For most comments on new code, use side=RIGHT.
- Use `start_line` + `line` for multi-line comments.
- Each comment body MUST start with a priority label: 🔴 Critical, 🟠 Important, 🟡 Suggestion, or 🟢 Nit.
- If there are no issues, set event=APPROVE and keep comments=[].

"""
