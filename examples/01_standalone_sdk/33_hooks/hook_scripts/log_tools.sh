#!/bin/bash
# PostToolUse hook: Log all tool usage
# Uses OPENHANDS_TOOL_NAME env var (no jq/python needed!)

# LOG_FILE should be set by the calling script
LOG_FILE="${LOG_FILE:-/tmp/tool_usage.log}"

echo "[$(date)] Tool used: $OPENHANDS_TOOL_NAME" >> "$LOG_FILE"
exit 0
