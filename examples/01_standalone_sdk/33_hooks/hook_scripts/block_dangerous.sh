#!/bin/bash
# PreToolUse hook: Block dangerous rm -rf commands
# Uses grep on raw JSON input (no jq needed)

input=$(cat)

# Block rm -rf commands by checking if the input contains the pattern
if echo "$input" | grep -q "rm -rf"; then
    echo '{"decision": "deny", "reason": "rm -rf commands are blocked for safety"}'
    exit 2  # Exit code 2 = block the operation
fi

exit 0  # Exit code 0 = allow the operation
