#!/bin/bash
# Stop hook: Require a summary.txt file before allowing agent to finish
# SUMMARY_FILE should be set by the calling script

SUMMARY_FILE="${SUMMARY_FILE:-./summary.txt}"

if [ ! -f "$SUMMARY_FILE" ]; then
    echo '{"decision": "deny", "additionalContext": "Create summary.txt first."}'
    exit 2
fi
exit 0
