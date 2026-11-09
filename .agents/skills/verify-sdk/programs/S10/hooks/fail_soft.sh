#!/bin/bash
# A hook that fails with exit 1: a non-blocking error.
cat > /dev/null
echo "soft failure" >&2
exit 1
