#!/bin/bash
# Stop hook: Run Python syntax check on all .py files in the workspace
# Returns deny if any Python file has syntax errors, with the error output as feedback
#
# This hook validates that the agent hasn't broken any Python files.
# Environment variable CHECK_DIR can override the default working directory.

CHECK_DIR="${CHECK_DIR:-.}"

# Find all Python files and check for syntax errors
ERRORS=""
while IFS= read -r -d '' file; do
    # Run python syntax check
    result=$(python3 -m py_compile "$file" 2>&1)
    if [ $? -ne 0 ]; then
        ERRORS="${ERRORS}\n${result}"
    fi
done < <(find "$CHECK_DIR" -name "*.py" -print0 2>/dev/null)

if [ -n "$ERRORS" ]; then
    # Escape the output for JSON
    ESCAPED_OUTPUT=$(echo -e "$ERRORS" | head -50 | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')
    echo "{\"decision\": \"deny\", \"additionalContext\": $ESCAPED_OUTPUT}"
    exit 2
fi

exit 0
