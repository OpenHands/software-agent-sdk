#!/usr/bin/env bash
set -euo pipefail

# Post or update a comment on a GitHub issue.
# Wrapper around `gh` CLI scoped to specific operations.
#
# Required environment variables:
#   GH_TOKEN or GITHUB_TOKEN - GitHub token with issues write permission
#
# Subcommands:
#   comment <repo> <issue_number> <body>  - Post a new comment
#   create-issue <repo> <title> <body>    - Create a new issue, prints issue number
#   view-issue <repo> <issue_number>      - View issue details
#   create-pr <repo> <title> <body> <head> <base> - Create a PR, prints PR URL
#
# Examples:
#   ./gh-comment.sh comment OpenHands/evaluation 42 "No leaks found in scan window."
#   ./gh-comment.sh create-issue OpenHands/evaluation "Secret Scan Tracker" "Tracking issue body"
#   ./gh-comment.sh create-pr OpenHands/OpenHands "fix: redact secrets in logs" "PR body" "fix/redact-secrets" "main"

export GH_HOST=github.com

# Prefer GH_TOKEN, fall back to GITHUB_TOKEN
export GH_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
if [[ -z "$GH_TOKEN" ]]; then
  echo "Error: GH_TOKEN or GITHUB_TOKEN must be set" >&2
  exit 1
fi

SUBCMD="${1:?Usage: gh-comment.sh <comment|create-issue|view-issue|create-pr> ...}"
shift

case "$SUBCMD" in
  comment)
    REPO="${1:?Usage: gh-comment.sh comment <repo> <issue_number> <body>}"
    ISSUE="${2:?Usage: gh-comment.sh comment <repo> <issue_number> <body>}"
    BODY="${3:?Usage: gh-comment.sh comment <repo> <issue_number> <body>}"

    if ! [[ "$ISSUE" =~ ^[0-9]+$ ]]; then
      echo "Error: issue_number must be numeric" >&2
      exit 1
    fi
    if ! [[ "$REPO" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]; then
      echo "Error: repo must be in owner/repo format" >&2
      exit 1
    fi

    gh issue comment "$ISSUE" --repo "$REPO" --body-file <(printf '%s' "$BODY")
    echo "Comment posted on ${REPO}#${ISSUE}" >&2
    ;;

  create-issue)
    REPO="${1:?Usage: gh-comment.sh create-issue <repo> <title> <body>}"
    TITLE="${2:?Usage: gh-comment.sh create-issue <repo> <title> <body>}"
    BODY="${3:?Usage: gh-comment.sh create-issue <repo> <title> <body>}"

    if ! [[ "$REPO" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]; then
      echo "Error: repo must be in owner/repo format" >&2
      exit 1
    fi

    RESULT=$(gh issue create --repo "$REPO" --title "$TITLE" --body-file <(printf '%s' "$BODY") --label "security")
    echo "$RESULT"
    echo "Issue created in ${REPO}" >&2
    ;;

  view-issue)
    REPO="${1:?Usage: gh-comment.sh view-issue <repo> <issue_number>}"
    ISSUE="${2:?Usage: gh-comment.sh view-issue <repo> <issue_number>}"

    if ! [[ "$ISSUE" =~ ^[0-9]+$ ]]; then
      echo "Error: issue_number must be numeric" >&2
      exit 1
    fi

    gh issue view "$ISSUE" --repo "$REPO"
    ;;

  create-pr)
    REPO="${1:?Usage: gh-comment.sh create-pr <repo> <title> <body> <head> <base>}"
    TITLE="${2:?Usage: gh-comment.sh create-pr <repo> <title> <body> <head> <base>}"
    BODY="${3:?Usage: gh-comment.sh create-pr <repo> <title> <body> <head> <base>}"
    HEAD="${4:?Usage: gh-comment.sh create-pr <repo> <title> <body> <head> <base>}"
    BASE="${5:?Usage: gh-comment.sh create-pr <repo> <title> <body> <head> <base>}"

    if ! [[ "$REPO" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]; then
      echo "Error: repo must be in owner/repo format" >&2
      exit 1
    fi

    RESULT=$(gh pr create --repo "$REPO" --title "$TITLE" --body-file <(printf '%s' "$BODY") --head "$HEAD" --base "$BASE")
    echo "$RESULT"
    echo "PR created in ${REPO}" >&2
    ;;

  *)
    echo "Error: unknown subcommand '$SUBCMD'. Use: comment, create-issue, view-issue, create-pr" >&2
    exit 1
    ;;
esac
