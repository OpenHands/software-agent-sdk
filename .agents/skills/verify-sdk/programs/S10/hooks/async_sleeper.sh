#!/bin/bash
# Async PostToolUse hook: save stdin and PID, then outlive the conversation.
cat > async_input.json
echo $$ > async_pid.txt
sleep 120
