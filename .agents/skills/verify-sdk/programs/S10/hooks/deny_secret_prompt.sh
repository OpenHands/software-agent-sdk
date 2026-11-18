#!/bin/bash
# UserPromptSubmit: block messages that ask for the secret.
if grep -qi "secret"; then
  echo "asking for the secret is not allowed" >&2
  echo '{"decision": "deny", "reason": "asking for the secret is not allowed"}'
  exit 2
fi
exit 0
