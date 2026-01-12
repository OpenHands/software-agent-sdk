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

## CRITICAL: Post ONE Single Review with All Comments

After completing your analysis, you MUST post your review as a **single API call** that
includes both the summary AND all inline comments together.

**IMPORTANT - Avoid Duplication:**
- Do NOT post individual inline comments separately and then a summary review
- Do NOT make multiple API calls - use ONE call with all comments bundled
- The summary in the review body should be brief (1-3 sentences) since details are in
  inline comments

### How to Post Your Review (Single API Call)

Use the GitHub CLI (`gh`) because it handles authentication and JSON payloads
more reliably.
The `GITHUB_TOKEN` environment variable is already available, and `gh` will use it
automatically.
You can install it if it's not already available in your environment.

**Post a review with multiple inline comments (REQUIRED approach):**

```bash
gh api \\
  -X POST \\
  repos/{repo_name}/pulls/{pr_number}/reviews \\
  -f commit_id='{commit_id}' \\
  -f event='COMMENT' \\
  -f body='Brief 1-3 sentence summary. Details are in inline comments below.' \\
  -f comments[][path]='path/to/file.py' \\
  -F comments[][line]=42 \\
  -f comments[][side]='RIGHT' \\
  -f comments[][body]='Your specific comment about this line.' \\
  -f comments[][path]='path/to/another_file.js' \\
  -F comments[][line]=15 \\
  -f comments[][side]='RIGHT' \\
  -f comments[][body]='Another specific comment.'
```

**Only if you have a single comment to make:**

```bash
gh api \\
  -X POST \\
  repos/{repo_name}/pulls/{pr_number}/reviews \\
  -f commit_id='{commit_id}' \\
  -f event='COMMENT' \\
  -f body='Brief summary.' \\
  -f comments[][path]='path/to/file.py' \\
  -F comments[][line]=42 \\
  -f comments[][side]='RIGHT' \\
  -f comments[][body]='Your specific comment about this line.'
```

### Fallback: Use GitHub HTTP API via `curl`

If `gh` is unavailable or fails, use `curl`. Remember: ONE API call with all comments.

**Post a review with all inline comments (single call):**

```bash
curl -X POST \\
  -H "Authorization: token $GITHUB_TOKEN" \\
  -H "Accept: application/vnd.github+json" \\
  -H "X-GitHub-Api-Version: 2022-11-28" \\
  "https://api.github.com/repos/{repo_name}/pulls/{pr_number}/reviews" \\
  -d '{{
    "commit_id": "{commit_id}",
    "body": "Brief 1-3 sentence summary. Details are in inline comments.",
    "event": "COMMENT",
    "comments": [
      {{
        "path": "path/to/file.py",
        "line": 42,
        "side": "RIGHT",
        "body": "Your specific comment about this line."
      }},
      {{
        "path": "path/to/another_file.js",
        "line": 15,
        "side": "RIGHT",
        "body": "Another specific comment."
      }}
    ]
  }}'
```

### Important Guidelines for Inline Comments:

1. **path**: Use the exact file path as shown in the diff
   (e.g., "src/utils/helper.py")
2. **line**: Use the line number in the NEW version of the file
   (the right side of the diff)
   - For added lines (starting with +), use the line number shown in the diff
   - You can use `grep -n` or examine the file directly to find the line number
3. **side**: Use "RIGHT" for commenting on new/added lines (most common),
   "LEFT" for deleted lines
4. **body**: Provide a clear, actionable comment.
   Be specific about what should be changed.

### Priority Levels for Review Comments:

**IMPORTANT**: Each inline comment MUST start with a priority label to help the PR
author understand the importance of each suggestion. Use one of these prefixes:

- **🔴 Critical**: Must be fixed before merging. Security vulnerabilities, bugs that
  will cause failures, data loss risks, or breaking changes.
- **🟠 Important**: Should be addressed. Logic errors, performance issues, missing
  error handling, or significant code quality concerns.
- **🟡 Suggestion**: Nice to have improvements. Better naming, code organization,
  or minor optimizations that would improve the code.
- **🟢 Nit**: Minor stylistic preferences. Formatting, comment wording, or trivial
  improvements that are optional.

**Example comment with priority:**
```
🟠 Important: This function doesn't handle the case when `user` is None, which could
cause an AttributeError in production.

```suggestion
if user is None:
    raise ValueError("User cannot be None")
```
```

**Another example:**
```
🟢 Nit: Consider using a more descriptive variable name for clarity.

```suggestion
user_count = len(users)
```
```

### Multi-Line Comments and Suggestions:

When your comment or suggestion spans multiple lines, you MUST specify a line range
using both `start_line` and `line` parameters. This is **critical** for multi-line
suggestions - if you only specify `line`, the suggestion will be misaligned.

**Parameters for multi-line comments:**
- **start_line**: The first line of the range (required for multi-line)
- **line**: The last line of the range
- **start_side**: Side of the first line (optional, defaults to same as `side`)
- **side**: Side of the last line

**Example with gh CLI for multi-line suggestion:**

```bash
gh api \\
  -X POST \\
  repos/{repo_name}/pulls/{pr_number}/reviews \\
  -f commit_id='{commit_id}' \\
  -f event='COMMENT' \\
  -f body='Found an issue spanning multiple lines.' \\
  -f comments[][path]='path/to/file.py' \\
  -F comments[][start_line]=10 \\
  -F comments[][line]=12 \\
  -f comments[][side]='RIGHT' \\
  -f comments[][body]='Consider this improvement:

```suggestion
first_line = "improved"
second_line = "code"
third_line = "here"
```'
```

**Example with curl for multi-line suggestion:**

```bash
curl -X POST \\
  -H "Authorization: token $GITHUB_TOKEN" \\
  -H "Accept: application/vnd.github+json" \\
  -H "X-GitHub-Api-Version: 2022-11-28" \\
  "https://api.github.com/repos/{repo_name}/pulls/{pr_number}/reviews" \\
  -d '{{
    "commit_id": "{commit_id}",
    "body": "Review summary.",
    "event": "COMMENT",
    "comments": [
      {{
        "path": "path/to/file.py",
        "start_line": 10,
        "line": 11,
        "side": "RIGHT",
        "body": "Suggestion:\\n\\n```suggestion\\nx = 1\\ny = 2\\n```"
      }}
    ]
  }}'
```

**IMPORTANT**: The number of lines in your suggestion block MUST match the number
of lines in the selected range (line - start_line + 1). If you select lines 10-12
(3 lines), your suggestion must also contain exactly 3 lines.

### Tips for Finding Line Numbers:
- The diff header shows line ranges: `@@ -old_start,old_count +new_start,new_count @@`
- Count lines from `new_start` for added/modified lines
- Use `grep -n "pattern" filename` to find exact line numbers in the current file
- Use `head -n LINE filename | tail -1` to verify the line content

### Using Suggestions for Small Code Changes:

For small, concrete code changes, use GitHub's suggestion feature. This allows the PR
author to apply your suggested change with one click. Format suggestions in the comment
body using the special markdown syntax:

~~~
```suggestion
def improved_function():
    return "better code here"
```
~~~

**Example: Including a suggestion in your review:**

Include the suggestion syntax in the `body` field of your inline comment within
the review:

```bash
gh api \\
  -X POST \\
  repos/{repo_name}/pulls/{pr_number}/reviews \\
  -f commit_id='{commit_id}' \\
  -f event='COMMENT' \\
  -f body='Found one issue with variable naming.' \\
  -f comments[][path]='path/to/file.py' \\
  -F comments[][line]=42 \\
  -f comments[][side]='RIGHT' \\
  -f comments[][body]='Consider using a more descriptive variable name:

```suggestion
user_count = len(users)
```'
```

**When to use suggestions:**
- Renaming variables or functions for clarity
- Fixing typos or formatting issues
- Small refactors (1-5 lines)
- Adding missing type hints or docstrings
- Fixing misleading or outdated comments
- Updating documentation or README content
- Correcting comments in configuration files (YAML, JSON, etc.)

**When NOT to use suggestions:**
- Large refactors requiring multiple file changes
- Architectural changes that need discussion
- Changes where multiple valid approaches exist

### What to Review:
- Focus on bugs, security issues, performance problems, and code quality
- Only post comments for actual issues or important suggestions
- Use the suggestion syntax for small, concrete code changes
- Be constructive and specific about what should be changed
- **Always include a priority label** (🔴 Critical, 🟠 Important, 🟡 Suggestion, 🟢 Nit)
  at the start of each inline comment
- If there are no issues, post a single review with an approval message (no inline
  comments needed)

### Your Task:
1. Analyze the diff and code carefully
2. Identify ALL specific issues on specific lines
3. Post ONE single review using the GitHub API that includes:
   - A brief summary in the review body (1-3 sentences)
   - ALL inline comments bundled in the same API call
4. Do NOT make multiple API calls or post comments separately

**CRITICAL**: Make exactly ONE API call to post your complete review.
Do NOT post individual comments first and then a summary - this creates duplication.
"""
