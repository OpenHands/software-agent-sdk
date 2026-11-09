#!/bin/bash
# Stop: deny until done.txt exists in the workspace.
cat > /dev/null
if [ ! -f done.txt ]; then
  echo '{"decision": "deny", "additionalContext": "Create done.txt first."}'
  exit 2
fi
exit 0
