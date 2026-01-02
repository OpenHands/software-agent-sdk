#!/bin/bash
# PreToolUse hook: Block dangerous rm -rf commands
# Uses jq for JSON parsing (needed for nested fields like tool_input.command)

input=$(cat)
command=$(echo "$input" | jq -r '.tool_input.command // ""')

# Block rm -rf commands
if [[ "$command" =~ "rm -rf" ]]; then
    echo '{"decision": "deny", "reason": "rm -rf commands are blocked for safety"}'
    exit 2  # Exit code 2 = block the operation
fi

exit 0  # Exit code 0 = allow the operation
