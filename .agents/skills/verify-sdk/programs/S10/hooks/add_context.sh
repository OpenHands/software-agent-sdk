#!/bin/bash
# UserPromptSubmit: add context to every user message.
cat > /dev/null
echo '{"additionalContext": "HOOK-CONTEXT: the build is green"}'
exit 0
