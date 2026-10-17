#!/bin/bash
# Append "<label> <stdin JSON>" to hook_calls.log in the workspace.
input=$(cat)
echo "$1 $input" >> hook_calls.log
exit 0
