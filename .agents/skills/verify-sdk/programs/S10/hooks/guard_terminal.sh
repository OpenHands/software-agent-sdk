#!/bin/bash
# PreToolUse (matcher "terminal"): record the hook environment, block `rm -rf`.
input=$(cat)
{
  echo "OPENHANDS_EVENT_TYPE=$OPENHANDS_EVENT_TYPE"
  echo "OPENHANDS_TOOL_NAME=$OPENHANDS_TOOL_NAME"
  echo "OPENHANDS_PROJECT_DIR=$OPENHANDS_PROJECT_DIR"
  echo "OPENHANDS_SESSION_ID=$OPENHANDS_SESSION_ID"
  echo "PWD=$PWD"
} > hook_env.txt
if echo "$input" | grep -q "rm -rf"; then
  echo '{"decision": "deny", "reason": "rm -rf is blocked by guard_terminal.sh"}'
  exit 2
fi
exit 0
