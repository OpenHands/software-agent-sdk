#!/bin/bash
# UserPromptSubmit hook: Inject git status when user asks about code changes

input=$(cat)

# Check if user is asking about changes, diff, or git
if echo "$input" | grep -qiE "(changes|diff|git|commit|modified)"; then
    # Get git status if in a git repo
    if git rev-parse --git-dir > /dev/null 2>&1; then
        status=$(git status --short 2>/dev/null | head -10)
        if [ -n "$status" ]; then
            # Escape for JSON
            escaped=$(echo "$status" | sed 's/"/\\"/g' | tr '\n' ' ')
            echo "{\"additionalContext\": \"Current git status: $escaped\"}"
        fi
    fi
fi
exit 0
